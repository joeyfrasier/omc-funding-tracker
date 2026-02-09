"""Background sync service — pulls data from Gmail, Worksuite DB, and MoneyCorp into reconciliation records."""
import logging
import traceback
from datetime import datetime

from recon_db import (
    upsert_from_remittance, upsert_from_invoice, upsert_from_funding,
    update_sync_state, cache_payruns,
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


def run_sync_cycle():
    """Run a full sync cycle across all sources."""
    logger.info("=== SYNC CYCLE STARTED ===")
    results = {}

    for name, fn in [('emails', sync_emails), ('invoices', sync_invoices), ('funding', sync_funding)]:
        try:
            results[name] = fn()
        except Exception as e:
            logger.error("Sync %s failed: %s\n%s", name, e, traceback.format_exc())
            results[name] = f"error: {e}"

    logger.info("=== SYNC CYCLE COMPLETE: %s ===", results)
    return results
