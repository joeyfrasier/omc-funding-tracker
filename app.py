"""Web dashboard for OMC Pay Run Funding Reconciliation."""
import json
import logging
import traceback
import threading
from collections import deque
from datetime import datetime
from decimal import Decimal
from flask import Flask, render_template, jsonify, request, Response
from gmail_client import load_processed
from db_client import get_omc_payments, get_omc_payruns, status_label
from email_db import get_all_emails, get_email_detail, get_stats
from reconciliation_service import run_pipeline, format_report_data, build_summary

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

app = Flask(__name__, static_folder='static', static_url_path='/static')

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

        def _progress(step, pct):
            _cache['run_step'] = step
            _cache['run_progress'] = pct

        result = run_pipeline(
            max_emails=max_emails,
            include_processed=include_processed,
            progress_callback=_progress,
        )

        _cache['last_run'] = datetime.now().isoformat()
        _cache['reports'] = result.reports
        _cache['emails_fetched'] = result.emails_fetched

        summary = build_summary(result.reports)
        return jsonify({
            'success': True,
            'emails_fetched': result.emails_fetched,
            'remittances_parsed': result.remittances_parsed,
            'reports': len(result.reports),
            'summary': summary,
            'message': result.message,
        })
    except Exception as e:
        _cache['errors'].append(str(e))
        _cache['run_step'] = f'ERROR: {str(e)}'
        logger.error("RECONCILIATION RUN FAILED: %s\n%s", e, traceback.format_exc())
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
        'summary': build_summary(reports),
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
    return jsonify(format_report_data(reports))


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


if __name__ == '__main__':
    import os
    from werkzeug.serving import WSGIRequestHandler
    logger.info("Starting Worksuite OMC Funding Reconciliation Dashboard on http://0.0.0.0:8501")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8501)), debug=False, use_reloader=False)
