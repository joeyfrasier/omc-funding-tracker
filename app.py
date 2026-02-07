"""Web dashboard for OMC Pay Run Funding Reconciliation."""
import json
import traceback
from datetime import datetime
from decimal import Decimal
from flask import Flask, render_template, jsonify, request
from gmail_client import fetch_all_remittances, fetch_emails, mark_processed, load_processed
from csv_parser import parse_email_attachments, Remittance
from matcher import reconcile, reconcile_batch, ReconciliationReport
from db_client import get_omc_payments, get_omc_payruns, status_label

app = Flask(__name__)

# In-memory cache of latest results
_cache = {
    'last_run': None,
    'reports': [],
    'emails_fetched': 0,
    'errors': [],
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
    try:
        max_emails = request.json.get('max_emails', 20) if request.json else 20
        
        # Step 1: Fetch emails
        emails = fetch_all_remittances(max_per_source=max_emails)
        
        # Step 2: Parse CSVs
        all_remittances = []
        for email in emails:
            if email.get('manual_review'):
                continue
            parsed = parse_email_attachments(email)
            all_remittances.extend(parsed)
        
        # Step 3: Reconcile
        reports = reconcile_batch(all_remittances)
        
        # Step 4: Mark processed
        processed_ids = [e['id'] for e in emails]
        mark_processed(processed_ids)
        
        # Cache results
        _cache['last_run'] = datetime.now().isoformat()
        _cache['reports'] = reports
        _cache['emails_fetched'] = len(emails)
        _cache['errors'] = []
        
        return jsonify({
            'success': True,
            'emails_fetched': len(emails),
            'remittances_parsed': len(all_remittances),
            'reports': len(reports),
            'summary': _build_summary(reports),
        })
    except Exception as e:
        _cache['errors'].append(str(e))
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


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
    })


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
    print("Starting OMC Funding Reconciliation Dashboard...")
    print("Access at http://0.0.0.0:8501")
    app.run(host='0.0.0.0', port=8501, debug=False)
