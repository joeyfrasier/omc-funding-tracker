"""Web dashboard for OMC Pay Run Funding Reconciliation."""
import json
import logging
import traceback
import threading
from collections import deque
from datetime import datetime
from decimal import Decimal
from flask import Flask, render_template, jsonify, request, Response
from gmail_client import fetch_all_remittances, fetch_emails, mark_processed, load_processed
from csv_parser import parse_email_attachments, Remittance
from matcher import reconcile, reconcile_batch, ReconciliationReport
from db_client import get_omc_payments, get_omc_payruns, status_label
from email_db import store_email, store_reconciliation, get_all_emails, get_email_detail, get_stats

# --- Activity Log (in-memory ring buffer for UI streaming) ---
_activity_log = deque(maxlen=200)
_activity_lock = threading.Lock()


class ActivityHandler(logging.Handler):
    """Captures log records into the activity log for UI display."""
    def emit(self, record):
        entry = {
            'ts': datetime.now().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': self.format(record),
        }
        with _activity_lock:
            _activity_log.append(entry)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
# Add activity handler to root so all module logs are captured
_activity_handler = ActivityHandler()
_activity_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s', '%H:%M:%S'))
logging.getLogger().addHandler(_activity_handler)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory cache of latest results
_cache = {
    'last_run': None,
    'reports': [],
    'emails_fetched': 0,
    'errors': [],
    'run_in_progress': False,
    'run_step': '',
    'run_progress': 0,  # 0-100
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


app.json_encoder = DecimalEncoder


@app.route('/')
def index():
    return render_template('dashboard.html')


@app.route('/api/run', methods=['POST'])
def run_reconciliation():
    """Fetch emails, parse CSVs, reconcile against DB."""
    if _cache['run_in_progress']:
        return jsonify({'success': False, 'error': 'A reconciliation run is already in progress'}), 409
    
    try:
        _cache['run_in_progress'] = True
        _cache['errors'] = []
        max_emails = request.json.get('max_emails', 20) if request.json else 20
        include_processed = request.json.get('include_processed', False) if request.json else False
        
        # Step 1: Fetch emails
        _cache['run_step'] = 'Fetching remittance emails...'
        _cache['run_progress'] = 10
        logger.info("=" * 60)
        logger.info("RECONCILIATION RUN STARTED (max_emails=%d, include_processed=%s)", max_emails, include_processed)
        logger.info("=" * 60)
        logger.info("Step 1/4: Fetching remittance emails from Gmail...")
        
        if include_processed:
            logger.info("Including already-processed emails (re-run mode)")
        
        emails = fetch_all_remittances(max_per_source=max_emails)
        _cache['run_progress'] = 30
        
        if not emails:
            logger.warning("No new emails found. All %d emails already processed.", len(load_processed()))
            logger.info("Tip: Use include_processed=true to re-process previously seen emails.")
            _cache['run_step'] = 'Complete (no new emails)'
            _cache['run_progress'] = 100
            _cache['run_in_progress'] = False
            _cache['last_run'] = datetime.now().isoformat()
            return jsonify({
                'success': True,
                'emails_fetched': 0,
                'remittances_parsed': 0,
                'reports': 0,
                'summary': _build_summary([]),
                'message': f'No new emails found. {len(load_processed())} emails already processed.',
            })
        
        logger.info("Step 1 complete: %d emails fetched", len(emails))
        
        # Store emails in database
        for email in emails:
            store_email(email)
        logger.info("Stored %d emails in database", len(emails))
        
        # Step 2: Parse CSVs
        _cache['run_step'] = f'Parsing {len(emails)} email attachments...'
        _cache['run_progress'] = 40
        logger.info("Step 2/4: Parsing CSV attachments from %d emails...", len(emails))
        all_remittances = []
        manual_count = 0
        parse_errors = 0
        for i, email in enumerate(emails, 1):
            if email.get('manual_review'):
                manual_count += 1
                logger.info("  [%d/%d] Skipping (manual review required): %s", i, len(emails), email.get('subject', '?')[:50])
                continue
            try:
                parsed = parse_email_attachments(email)
                all_remittances.extend(parsed)
                if parsed:
                    logger.info("  [%d/%d] Parsed %d remittance(s): %s", i, len(emails), len(parsed), email.get('subject', '?')[:50])
                else:
                    logger.warning("  [%d/%d] No CSVs found in: %s", i, len(emails), email.get('subject', '?')[:50])
            except Exception as e:
                parse_errors += 1
                logger.error("  [%d/%d] Parse error for %s: %s", i, len(emails), email.get('subject', '?')[:50], e)
        
        _cache['run_progress'] = 60
        logger.info("Step 2 complete: %d remittances parsed, %d manual review, %d parse errors", len(all_remittances), manual_count, parse_errors)
        
        if not all_remittances:
            logger.warning("No remittances could be parsed from the fetched emails.")
            _cache['run_step'] = 'Complete (no parseable remittances)'
            _cache['run_progress'] = 100
            _cache['run_in_progress'] = False
            _cache['last_run'] = datetime.now().isoformat()
            return jsonify({
                'success': True,
                'emails_fetched': len(emails),
                'remittances_parsed': 0,
                'reports': 0,
                'summary': _build_summary([]),
                'message': 'Emails fetched but no CSVs could be parsed.',
            })
        
        # Step 3: Reconcile
        _cache['run_step'] = f'Reconciling {len(all_remittances)} remittances against database...'
        _cache['run_progress'] = 70
        logger.info("Step 3/4: Reconciling %d remittances against Worksuite database...", len(all_remittances))
        logger.info("  Opening SSH tunnel to aggregate DB...")
        reports = reconcile_batch(all_remittances)
        _cache['run_progress'] = 90
        logger.info("Step 3 complete: %d reconciliation reports generated", len(reports))
        
        # Store reconciliation results in database
        for report in reports:
            email_id = report.remittance.source_email_id
            if email_id:
                store_reconciliation(email_id, report)
        logger.info("Stored %d reconciliation reports in database", len(reports))
        
        # Step 4: Mark processed
        _cache['run_step'] = 'Marking emails as processed...'
        logger.info("Step 4/4: Marking %d emails as processed...", len(emails))
        processed_ids = [e['id'] for e in emails]
        mark_processed(processed_ids)
        logger.info("Step 4 complete: %d emails marked as processed (total processed: %d)", len(processed_ids), len(load_processed()))
        
        # Cache results
        _cache['last_run'] = datetime.now().isoformat()
        _cache['reports'] = reports
        _cache['emails_fetched'] = len(emails)
        _cache['run_step'] = 'Complete'
        _cache['run_progress'] = 100
        
        summary = _build_summary(reports)
        logger.info("=" * 60)
        logger.info("RECONCILIATION RUN COMPLETE")
        logger.info("  Emails: %d | Remittances: %d | Reports: %d", len(emails), len(all_remittances), len(reports))
        logger.info("  Matched: %d | Mismatched: %d | Not Found: %d", summary.get('matched', 0), summary.get('mismatched', 0), summary.get('not_found', 0))
        logger.info("  Match Rate: %s | Total Value: $%s", summary.get('match_rate', 'N/A'), f"{summary.get('total_remittance_value', 0):,.2f}")
        if parse_errors:
            logger.warning("  ⚠️ %d emails had parse errors", parse_errors)
        logger.info("=" * 60)
        
        return jsonify({
            'success': True,
            'emails_fetched': len(emails),
            'remittances_parsed': len(all_remittances),
            'reports': len(reports),
            'summary': summary,
        })
    except Exception as e:
        _cache['errors'].append(str(e))
        _cache['run_step'] = f'ERROR: {str(e)}'
        logger.error("=" * 60)
        logger.error("RECONCILIATION RUN FAILED: %s", e)
        logger.error(traceback.format_exc())
        logger.error("=" * 60)
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500
    finally:
        _cache['run_in_progress'] = False


@app.route('/api/status')
def status():
    """Get current status and cached results."""
    reports = _cache['reports']
    return jsonify({
        'last_run': _cache['last_run'],
        'emails_fetched': _cache['emails_fetched'],
        'reports_count': len(reports),
        'processed_count': len(load_processed()),
        'summary': _build_summary(reports),
        'errors': _cache['errors'],
        'run_in_progress': _cache['run_in_progress'],
        'run_step': _cache['run_step'],
        'run_progress': _cache['run_progress'],
    })


@app.route('/api/activity')
def activity():
    """Get recent activity log entries."""
    since = request.args.get('since', '')
    with _activity_lock:
        entries = list(_activity_log)
    if since:
        entries = [e for e in entries if e['ts'] > since]
    return jsonify(entries[-50:])


@app.route('/api/reports')
def get_reports():
    """Get detailed reconciliation reports."""
    reports = _cache['reports']
    result = []
    for r in reports:
        matches = []
        for m in r.matches:
            matches.append({
                'nvc_code': m.nvc_code,
                'description': m.remittance_line.description,
                'company': m.remittance_line.company,
                'remittance_amount': float(m.remittance_amount),
                'db_amount': m.db_amount,
                'difference': m.difference,
                'status': m.status,
                'notes': m.notes,
                'tenant': m.db_record.get('tenant', '').replace('.worksuite.com', '') if m.db_record else '',
                'db_status': status_label(m.db_record.get('status', -1)) if m.db_record else '',
                'payrun_ref': m.db_record.get('payrun_reference', '') if m.db_record else '',
            })
        
        result.append({
            'subject': r.remittance.subject,
            'agency': r.remittance.agency,
            'account': r.remittance.account_number,
            'date': r.remittance.payment_date,
            'total': float(r.remittance.payment_amount),
            'source': r.remittance.source_type,
            'matched': r.matched_count,
            'mismatched': r.mismatched_count,
            'not_found': r.not_found_count,
            'total_lines': len(r.matches),
            'matches': matches,
        })
    
    return jsonify(result)


@app.route('/api/processed')
def processed_emails():
    """Get all processed emails from the database."""
    limit = request.args.get('limit', 200, type=int)
    offset = request.args.get('offset', 0, type=int)
    emails = get_all_emails(limit=limit, offset=offset)
    stats = get_stats()
    return jsonify({'emails': emails, 'stats': stats})


@app.route('/api/processed/<email_id>')
def processed_email_detail(email_id):
    """Get full detail for a processed email."""
    detail = get_email_detail(email_id)
    if not detail:
        return jsonify({'error': 'Email not found'}), 404
    return jsonify(detail)


@app.route('/processed')
def processed_view():
    """Processed emails display page."""
    return render_template('processed.html')


@app.route('/api/db/payments')
def db_payments():
    """Get recent OMC payments from DB."""
    days = request.args.get('days', 30, type=int)
    try:
        payments = get_omc_payments(days_back=days)
        return jsonify({'count': len(payments), 'payments': payments[:200]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/payruns')
def db_payruns():
    """Get recent OMC pay runs from DB."""
    days = request.args.get('days', 30, type=int)
    try:
        payruns = get_omc_payruns(days_back=days)
        return jsonify({'count': len(payruns), 'payruns': payruns[:100]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _build_summary(reports):
    """Build aggregate summary from reports."""
    if not reports:
        return {
            'total_remittances': 0,
            'total_lines': 0,
            'matched': 0,
            'mismatched': 0,
            'not_found': 0,
            'total_remittance_value': 0,
            'agencies': [],
        }
    
    agencies = {}
    total_matched = 0
    total_mismatched = 0
    total_not_found = 0
    total_lines = 0
    total_value = 0
    
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
        'match_rate': f"{total_matched/total_lines*100:.1f}%" if total_lines else "N/A",
        'agencies': sorted(agencies.values(), key=lambda a: a['total'], reverse=True),
    }


if __name__ == '__main__':
    logger.info("Starting OMC Funding Reconciliation Dashboard on http://0.0.0.0:8501")
    app.run(host='0.0.0.0', port=8501, debug=False)
