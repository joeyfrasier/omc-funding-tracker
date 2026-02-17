"""Shared reconciliation pipeline — fetch emails → parse CSVs → reconcile → store.

Used by both FastAPI (api.py) and Flask (app.py) to avoid duplicating
the 4-step reconciliation workflow.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from gmail_client import fetch_all_remittances, fetch_emails, load_processed, mark_processed
from csv_parser import parse_email_attachments
from matcher import reconcile_batch, ReconciliationReport
from email_db import store_email, store_reconciliation

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""
    success: bool = True
    message: str = ''
    emails_fetched: int = 0
    remittances_parsed: int = 0
    manual_review: int = 0
    parse_errors: int = 0
    reports: List[ReconciliationReport] = field(default_factory=list)


def run_pipeline(
    max_emails: int = 20,
    include_processed: bool = False,
    progress_callback: Optional[callable] = None,
) -> ReconciliationResult:
    """Run the full reconciliation pipeline.

    Args:
        max_emails: Max emails to fetch per source.
        include_processed: Whether to re-process previously seen emails.
        progress_callback: Optional fn(step: str, pct: int) for progress updates.

    Returns:
        ReconciliationResult with reports and counts.
    """
    result = ReconciliationResult()

    def _progress(step: str, pct: int):
        if progress_callback:
            progress_callback(step, pct)

    logger.info("=" * 60)
    logger.info("RECONCILIATION RUN STARTED (max=%d, include_processed=%s)", max_emails, include_processed)

    # Step 1: Fetch emails
    _progress('Fetching remittance emails...', 10)
    if include_processed:
        emails = []
        for key in ["oasys", "d365_ach"]:
            try:
                batch = fetch_emails(key, max_results=max_emails, include_processed=True)
                emails.extend(batch)
            except Exception as e:
                logger.warning("Fetch error for %s: %s", key, e)
    else:
        emails = fetch_all_remittances(max_per_source=max_emails)

    result.emails_fetched = len(emails)

    if not emails:
        processed_count = len(load_processed())
        result.message = f"No new emails. {processed_count} already processed."
        _progress('Complete (no new emails)', 100)
        logger.info("No new emails found. %d already processed.", processed_count)
        return result

    _progress(f'Storing {len(emails)} emails...', 25)
    for e in emails:
        store_email(e)

    # Step 2: Parse CSVs
    _progress(f'Parsing {len(emails)} email attachments...', 40)
    logger.info("Parsing CSV attachments from %d emails...", len(emails))
    all_remittances = []
    for i, email in enumerate(emails, 1):
        if email.get('manual_review'):
            result.manual_review += 1
            continue
        try:
            parsed = parse_email_attachments(email)
            all_remittances.extend(parsed)
            if parsed:
                logger.info("  [%d/%d] Parsed %d remittance(s): %s",
                            i, len(emails), len(parsed), email.get('subject', '?')[:50])
        except Exception as e:
            result.parse_errors += 1
            logger.warning("  [%d/%d] Parse error: %s", i, len(emails), e)

    result.remittances_parsed = len(all_remittances)

    if not all_remittances:
        result.message = "Emails fetched but no CSVs could be parsed."
        _progress('Complete (no parseable remittances)', 100)
        return result

    # Step 3: Reconcile
    _progress(f'Reconciling {len(all_remittances)} remittances...', 70)
    logger.info("Reconciling %d remittances against Worksuite database...", len(all_remittances))
    reports = reconcile_batch(all_remittances)
    result.reports = reports

    # Store reconciliation results
    for report in reports:
        eid = report.remittance.source_email_id
        if eid:
            store_reconciliation(eid, report)

    # Step 4: Mark processed
    _progress('Marking emails as processed...', 90)
    mark_processed([e['id'] for e in emails])

    _progress('Complete', 100)
    logger.info("RECONCILIATION COMPLETE: %d reports from %d emails", len(reports), len(emails))
    return result


def format_report_data(reports: List[ReconciliationReport]) -> list:
    """Convert ReconciliationReport objects to JSON-serializable dicts."""
    report_data = []
    for r in reports:
        matches = []
        for m in r.matches:
            matches.append({
                'nvc_code': m.nvc_code,
                'contractor': m.remittance_line.description,
                'company': m.remittance_line.company,
                'remittance_amount': float(m.remittance_amount),
                'db_amount': m.db_amount,
                'difference': m.difference,
                'status': m.status,
                'notes': m.notes,
                'tenant': m.db_record.get('tenant', '').replace('.worksuite.com', '') if m.db_record else '',
                'db_status': m.db_record.get('status', '') if m.db_record else '',
                'payrun_ref': m.db_record.get('payrun_reference', '') if m.db_record else '',
            })
        report_data.append({
            'agency': r.remittance.agency or r.remittance.subject[:40],
            'subject': r.remittance.subject,
            'total': float(r.remittance.payment_amount),
            'source': r.remittance.source_type,
            'account': r.remittance.account_number,
            'date': r.remittance.payment_date,
            'matched': r.matched_count,
            'mismatched': r.mismatched_count,
            'not_found': r.not_found_count,
            'total_lines': len(r.matches),
            'matches': matches,
        })
    return report_data


def build_summary(reports: List[ReconciliationReport]) -> dict:
    """Build aggregate summary from reconciliation reports."""
    if not reports:
        return {
            'total_remittances': 0, 'total_lines': 0,
            'matched': 0, 'mismatched': 0, 'not_found': 0,
            'total_remittance_value': 0, 'match_rate': 'N/A', 'agencies': [],
        }

    agencies = {}
    total_matched = total_mismatched = total_not_found = total_lines = 0
    total_value = 0.0

    for r in reports:
        total_matched += r.matched_count
        total_mismatched += r.mismatched_count
        total_not_found += r.not_found_count
        total_lines += len(r.matches)
        total_value += float(r.remittance.payment_amount)

        agency = r.remittance.agency or 'Unknown'
        if agency not in agencies:
            agencies[agency] = {'name': agency, 'remittances': 0, 'total': 0, 'matched': 0, 'issues': 0}
        agencies[agency]['remittances'] += 1
        agencies[agency]['total'] += float(r.remittance.payment_amount)
        agencies[agency]['matched'] += r.matched_count
        agencies[agency]['issues'] += r.mismatched_count + r.not_found_count

    return {
        'total_remittances': len(reports),
        'total_lines': total_lines,
        'matched': total_matched,
        'mismatched': total_mismatched,
        'not_found': total_not_found,
        'total_remittance_value': total_value,
        'match_rate': f"{total_matched / total_lines * 100:.1f}%" if total_lines else "N/A",
        'agencies': sorted(agencies.values(), key=lambda a: a['total'], reverse=True),
    }
