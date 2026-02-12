"""Background sync service — pulls data from Gmail, Worksuite DB, and MoneyCorp into reconciliation records.

Sync cycle order:
  1. sync_emails()              — Remittance emails (Leg 1)
  2. sync_invoices()            — Worksuite DB (Leg 2)
  3. sync_received_payments()   — MoneyCorp inbound USD (Leg 3)
  4. sync_payments()            — MoneyCorp outbound payments (Leg 4)
  5. run_funding_matcher()      — Match received payments ↔ remittance emails
"""
import logging
import traceback
from datetime import datetime, timedelta

from recon_db import (
    upsert_from_remittance, upsert_from_invoice, upsert_from_funding,
    upsert_received_payment, link_received_payment_to_nvc,
    match_received_payment, get_received_payments,
    update_sync_state, cache_payruns, cache_invoices,
    AGENCY_ALIASES, _get_conn, RECON_DB_PATH,
)

logger = logging.getLogger(__name__)


def sync_emails():
    """Fetch remittance emails from Gmail, parse CSVs, upsert into recon DB."""
    from gmail_client import fetch_all_remittances
    from csv_parser import parse_email_attachments
    from email_db import store_email

    count = 0
    try:
        emails = fetch_all_remittances(max_per_source=50)
        logger.info("sync_emails: fetched %d emails", len(emails))

        for email in emails:
            # Store in legacy email DB too
            try:
                store_email(email)
            except Exception:
                pass

            if email.get('manual_review'):
                continue

            try:
                remittances = parse_email_attachments(email)
            except Exception as e:
                logger.warning("sync_emails: parse error for %s: %s", email.get('id', '?')[:12], e)
                continue

            for rem in remittances:
                for line in rem.lines:
                    if not line.nvc_code:
                        continue
                    upsert_from_remittance(
                        nvc_code=line.nvc_code,
                        amount=float(line.amt_paid),
                        date=rem.payment_date,
                        source=rem.source_type,
                        email_id=rem.source_email_id,
                    )
                    count += 1

        update_sync_state('emails', count, 'ok')
        logger.info("sync_emails: upserted %d remittance lines", count)
    except Exception as e:
        logger.error("sync_emails failed: %s", e)
        update_sync_state('emails', count, f'error: {str(e)[:80]}')
        raise
    return count


def sync_invoices():
    """Fetch OMC payments from Worksuite DB, upsert into recon DB."""
    from db_client import get_omc_payments, get_omc_payruns, status_label

    count = 0
    try:
        payments = get_omc_payments(days_back=60)
        logger.info("sync_invoices: fetched %d payments", len(payments))

        for p in payments:
            nvc_code = p.get('nvc_code')
            if not nvc_code:
                continue
            upsert_from_invoice(
                nvc_code=nvc_code,
                amount=float(p.get('total_amount') or 0),
                status=status_label(p.get('status')),
                tenant=p.get('tenant', '').replace('.worksuite.com', ''),
                payrun_ref=str(p.get('payrun_id', '')),
                currency=p.get('currency', ''),
            )
            count += 1

        # Cache raw invoices
        try:
            cache_invoices([{
                'nvc_code': p.get('nvc_code'),
                'invoice_number': p.get('invoice_number'),
                'total_amount': float(p.get('total_amount') or 0),
                'currency': p.get('currency', ''),
                'status': p.get('status'),
                'status_label': status_label(p.get('status')),
                'paid_date': str(p.get('paid_date', '') or ''),
                'processing_date': str(p.get('processing_date', '') or ''),
                'in_flight_date': str(p.get('in_flight_date', '') or ''),
                'tenant': p.get('tenant', '').replace('.worksuite.com', ''),
                'payrun_id': str(p.get('payrun_id', '')),
                'created_at': str(p.get('created_at', '')),
            } for p in payments if p.get('nvc_code')])
            logger.info("sync_invoices: cached %d invoices", len(payments))
        except Exception as e:
            logger.warning("sync_invoices: invoice cache failed: %s", e)

        # Also cache pay runs
        try:
            payruns = get_omc_payruns(days_back=60)
            cache_payruns([{
                'id': pr.get('id'),
                'reference': pr.get('reference'),
                'tenant': pr.get('tenant', '').replace('.worksuite.com', ''),
                'status': pr.get('status'),
                'payment_count': pr.get('payment_count'),
                'total_amount': float(pr.get('total_amount') or 0),
                'created_at': str(pr.get('created_at', '')),
            } for pr in payruns])
            logger.info("sync_invoices: cached %d payruns", len(payruns))
        except Exception as e:
            logger.warning("sync_invoices: payrun cache failed: %s", e)

        update_sync_state('invoices', count, 'ok')
        logger.info("sync_invoices: upserted %d invoice records", count)
    except Exception as e:
        logger.error("sync_invoices failed: %s", e)
        update_sync_state('invoices', count, f'error: {str(e)[:80]}')
        raise
    return count


def sync_funding():
    """Fetch MoneyCorp payments across all OMC accounts, upsert into recon DB."""
    from moneycorp_client import get_all_omc_payments

    count = 0
    try:
        payments = get_all_omc_payments()
        logger.info("sync_funding: fetched %d MoneyCorp payments", len(payments))

        for p in payments:
            nvc_code = p.get('nvc_code')
            if not nvc_code:
                continue
            upsert_from_funding(
                nvc_code=nvc_code,
                amount=float(p.get('amount') or 0),
                account_id=str(p.get('account_id', '')),
                date=p.get('payment_date', ''),
                currency=p.get('currency', ''),
                status=p.get('status', ''),
                recipient=p.get('recipient_name', ''),
                recipient_country=p.get('recipient_country', ''),
            )
            count += 1

        update_sync_state('funding', count, 'ok')
        logger.info("sync_funding: upserted %d funding records with NVC codes", count)
    except Exception as e:
        logger.error("sync_funding failed: %s", e)
        update_sync_state('funding', count, f'error: {str(e)[:80]}')
        raise
    return count


def sync_received_payments():
    """Fetch MoneyCorp receivedPayments across all OMC accounts (Leg 3 — inbound USD)."""
    from moneycorp_client import get_all_omc_received_payments

    count = 0
    try:
        payments = get_all_omc_received_payments()
        logger.info("sync_received_payments: fetched %d received payments", len(payments))

        for p in payments:
            upsert_received_payment(
                payment_id=p['id'],
                account_id=p['account_id'],
                account_name=p.get('account_name', ''),
                amount=p['amount'],
                currency=p.get('currency', 'USD'),
                payment_date=p.get('payment_date', ''),
                payment_status=p.get('payment_status', ''),
                payer_name=p.get('payer_name', ''),
                raw_info=p.get('raw_info', ''),
                msl_reference=p.get('msl_reference', ''),
                created_on=p.get('created_on', ''),
            )
            count += 1

        update_sync_state('received_payments', count, 'ok')
        logger.info("sync_received_payments: upserted %d records", count)
    except Exception as e:
        logger.error("sync_received_payments failed: %s", e)
        update_sync_state('received_payments', count, f'error: {str(e)[:80]}')
        raise
    return count


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy comparison."""
    import re
    name = name.upper().strip()
    # Remove common suffixes
    for suffix in [' LLC', ' INC', ' INC.', ' LTD', ' LTD.', ' CORP', ' CORP.']:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    name = re.sub(r'[^A-Z0-9 ]', '', name)
    return name


def _payer_matches_agency(payer_name: str, agency_name: str) -> float:
    """Score how well a payer name matches an agency name. Returns 0.0-1.0."""
    if not payer_name or not agency_name:
        return 0.0
    
    pn = _normalize_name(payer_name)
    an = _normalize_name(agency_name)
    
    if pn == an:
        return 1.0
    
    # Check aliases
    for canonical, aliases in AGENCY_ALIASES.items():
        canon_norm = _normalize_name(canonical)
        alias_norms = [_normalize_name(a) for a in aliases]
        if pn == canon_norm or pn in alias_norms:
            if an == canon_norm or an in alias_norms:
                return 0.9
    
    # Substring match
    if pn in an or an in pn:
        return 0.6
    
    # Word overlap
    pn_words = set(pn.split())
    an_words = set(an.split())
    if pn_words and an_words:
        overlap = len(pn_words & an_words) / max(len(pn_words), len(an_words))
        if overlap > 0.5:
            return overlap * 0.7
    
    return 0.0


def run_funding_matcher():
    """Match received payments to remittance emails using amount + date + payer name.
    
    When matched, cascade to link all NVC codes from that remittance email.
    """
    import sqlite3
    
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    
    # Get unmatched received payments
    unmatched_rps = conn.execute(
        "SELECT * FROM received_payments WHERE match_status = 'unmatched'"
    ).fetchall()
    
    if not unmatched_rps:
        conn.close()
        logger.info("run_funding_matcher: no unmatched received payments")
        return 0
    
    # Get remittance emails with totals from the email DB
    # We need to aggregate remittance totals per email from reconciliation_records
    email_totals = {}
    email_rows = conn.execute("""
        SELECT remittance_email_id, 
               SUM(remittance_amount) as total_amount,
               MIN(remittance_date) as earliest_date,
               remittance_source
        FROM reconciliation_records 
        WHERE remittance_email_id IS NOT NULL 
        GROUP BY remittance_email_id
    """).fetchall()
    
    for er in email_rows:
        email_totals[er['remittance_email_id']] = {
            'total_amount': er['total_amount'],
            'date': er['earliest_date'],
            'source': er['remittance_source'],
        }
    
    # Also try to get agency names from the processed emails DB
    email_agencies = {}
    try:
        import sqlite3 as _sq
        from pathlib import Path
        econn = _sq.connect(str(Path('data/processed_emails.db')))
        econn.row_factory = _sq.Row
        for row in econn.execute("SELECT id, subject, sender FROM emails").fetchall():
            # Extract agency from subject line
            subj = row['subject'] or ''
            email_agencies[row['id']] = subj
        econn.close()
    except Exception:
        pass
    
    matched_count = 0
    
    for rp in unmatched_rps:
        rp = dict(rp)
        best_match = None
        best_score = 0.0
        
        for email_id, email_info in email_totals.items():
            score = 0.0
            
            # Amount match (weight 0.5)
            rp_amt = rp['amount']
            em_amt = email_info['total_amount']
            if em_amt and rp_amt:
                diff_pct = abs(rp_amt - em_amt) / max(rp_amt, em_amt) if max(rp_amt, em_amt) > 0 else 1
                if diff_pct <= 0.0001:  # exact
                    score += 0.5
                elif diff_pct <= 0.01:  # within 1%
                    score += 0.35
                elif diff_pct <= 0.05:  # within 5%
                    score += 0.15
            
            # Date match (weight 0.2)
            if rp.get('payment_date') and email_info.get('date'):
                try:
                    rp_date = rp['payment_date'][:10]
                    em_date = email_info['date'][:10]
                    from datetime import datetime as dt
                    d1 = dt.strptime(rp_date, '%Y-%m-%d') if '-' in rp_date else dt.strptime(rp_date, '%m/%d/%Y')
                    d2 = dt.strptime(em_date, '%Y-%m-%d') if '-' in em_date else dt.strptime(em_date, '%m/%d/%Y')
                    day_diff = abs((d1 - d2).days)
                    if day_diff == 0:
                        score += 0.2
                    elif day_diff <= 1:
                        score += 0.16
                    elif day_diff <= 3:
                        score += 0.1
                    elif day_diff <= 7:
                        score += 0.04
                except Exception:
                    pass
            
            # Payer name match (weight 0.3)
            agency_str = email_agencies.get(email_id, '')
            payer_score = _payer_matches_agency(rp.get('payer_name', ''), agency_str)
            score += 0.3 * payer_score
            
            if score > best_score:
                best_score = score
                best_match = email_id
        
        # Auto-match if score >= 0.8
        if best_match and best_score >= 0.8:
            match_received_payment(rp['id'], best_match, best_score, 'auto_amount_date_payer')
            
            # Cascade: link all NVC codes from this email to this received payment
            nvc_rows = conn.execute(
                "SELECT nvc_code FROM reconciliation_records WHERE remittance_email_id = ?",
                (best_match,)
            ).fetchall()
            for nr in nvc_rows:
                link_received_payment_to_nvc(
                    nr['nvc_code'], rp['id'], rp['amount'], rp.get('payment_date', '')
                )
            
            matched_count += 1
            logger.info("Matched received payment %s ($%.2f) → email %s (score: %.2f, %d NVCs)",
                        rp['id'], rp['amount'], best_match, best_score, len(nvc_rows))
        elif best_match and best_score >= 0.5:
            # Mark as suggested (partial match)
            conn.execute(
                "UPDATE received_payments SET match_status = 'suggested', notes = ? WHERE id = ?",
                (f"Suggested: email {best_match} (score: {best_score:.2f})", rp['id'])
            )
            conn.commit()
    
    conn.close()
    logger.info("run_funding_matcher: matched %d of %d unmatched received payments", matched_count, len(unmatched_rps))
    return matched_count


def run_sync_cycle():
    """Run a full sync cycle across all sources."""
    logger.info("=== SYNC CYCLE STARTED ===")
    results = {}

    for name, fn in [
        ('emails', sync_emails),
        ('invoices', sync_invoices),
        ('received_payments', sync_received_payments),
        ('funding', sync_funding),  # outbound payments (Leg 4)
    ]:
        try:
            results[name] = fn()
        except Exception as e:
            logger.error("Sync %s failed: %s\n%s", name, e, traceback.format_exc())
            results[name] = f"error: {e}"

    # Run funding matcher after all data is synced
    try:
        results['funding_matcher'] = run_funding_matcher()
    except Exception as e:
        logger.error("Funding matcher failed: %s\n%s", e, traceback.format_exc())
        results['funding_matcher'] = f"error: {e}"

    logger.info("=== SYNC CYCLE COMPLETE: %s ===", results)
    return results
