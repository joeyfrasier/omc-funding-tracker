"""Extract reconciliation data for executive summary."""
import sqlite3
import json

conn = sqlite3.connect('data/recon.db')
conn.row_factory = sqlite3.Row

data = {}

# Match status breakdown
data['match_status'] = {}
for r in conn.execute('SELECT match_status, COUNT(*) as cnt FROM reconciliation_records GROUP BY match_status ORDER BY cnt DESC'):
    data['match_status'][r['match_status']] = r['cnt']

# Time series by month
rows = conn.execute("""
    SELECT
        CASE
            WHEN payment_date IS NOT NULL AND payment_date != '' THEN substr(payment_date, 1, 7)
            WHEN remittance_date IS NOT NULL AND remittance_date != '' THEN substr(remittance_date, 1, 7)
            ELSE substr(first_seen_at, 1, 7)
        END as month,
        match_status,
        COUNT(*) as cnt
    FROM reconciliation_records
    GROUP BY month, match_status
    ORDER BY month, match_status
""").fetchall()
months = {}
for r in rows:
    m = r['month']
    if m not in months:
        months[m] = {}
    months[m][r['match_status']] = r['cnt']
data['by_month'] = {}
for m in sorted(months):
    total = sum(months[m].values())
    reconciled = sum(months[m].get(s, 0) for s in ['full_4way', '2way_matched', '3way_no_funding', '3way_awaiting_payment'])
    inv_pay = months[m].get('invoice_payment_only', 0)
    data['by_month'][m] = {
        'total': total,
        'reconciled': reconciled,
        'reconciled_pct': round(reconciled / total * 100, 1) if total else 0,
        'invoice_payment_only': inv_pay,
        'inv_pay_pct': round((inv_pay + reconciled) / total * 100, 1) if total else 0,
        'breakdown': dict(months[m])
    }

# Tenant breakdown for invoice_payment_only
data['tenant_inv_pay'] = []
for r in conn.execute("""
    SELECT invoice_tenant, COUNT(*) as cnt,
           COALESCE(SUM(invoice_amount), 0) as inv_total,
           COALESCE(SUM(payment_amount), 0) as pay_total,
           SUM(CASE WHEN ABS(COALESCE(invoice_amount,0) - COALESCE(payment_amount,0)) < 0.02 THEN 1 ELSE 0 END) as exact_match
    FROM reconciliation_records
    WHERE match_status = 'invoice_payment_only'
    GROUP BY invoice_tenant ORDER BY inv_total DESC
"""):
    data['tenant_inv_pay'].append({
        'tenant': r['invoice_tenant'],
        'count': r['cnt'],
        'inv_total': round(r['inv_total'], 2),
        'pay_total': round(r['pay_total'], 2),
        'exact_match': r['exact_match'],
        'fx_mismatch': r['cnt'] - r['exact_match']
    })

# Received payments summary
data['received_by_status'] = {}
for r in conn.execute('SELECT match_status, COUNT(*) as cnt, SUM(amount) as total FROM received_payments GROUP BY match_status'):
    data['received_by_status'][r['match_status']] = {'count': r['cnt'], 'total': round(r['total'], 2)}

# Received payments by account vs invoice totals
data['received_by_account'] = []
for r in conn.execute("""
    SELECT account_name, SUM(amount) as total, COUNT(*) as cnt
    FROM received_payments GROUP BY account_name ORDER BY total DESC
"""):
    # Find matching tenant invoices
    tenant_map = {
        'Omnicom Healthcare': 'omcohg',
        'Omnicom Advertising - BBDO': 'omcbbdo',
        'Omnicom Public Relations': 'omnicomprg',
        'Omnicom Advertising - DDB': 'omnicomddb',
        'Omnicom Media': 'omnicommedia',
        'Omnicom Advertising - OAC': 'omnicomoac',
        'Omnicom Flywheel Digital': 'omcflywheel',
        'Omnicom Production': 'omnicom',
        'Omnicom Branding Consulting': 'omnicombranding',
        'Omnicom Precision Marketing': 'omnicomprecision',
        'Omnicom Advertising - TBWA': 'omnicomtbwa',
    }
    tenant_slug = tenant_map.get(r['account_name'], '')
    inv_row = conn.execute(
        "SELECT COALESCE(SUM(invoice_amount), 0) as total FROM reconciliation_records WHERE invoice_tenant = ?",
        (tenant_slug,)
    ).fetchone()
    inv_total = round(inv_row['total'], 2) if inv_row else 0
    data['received_by_account'].append({
        'account': r['account_name'],
        'tenant': tenant_slug,
        'received_total': round(r['total'], 2),
        'received_count': r['cnt'],
        'invoice_total': inv_total,
        'coverage_pct': round(r['total'] / inv_total * 100, 1) if inv_total > 0 else 0
    })

# Invoice-only status breakdown
data['invoice_only_status'] = {}
for r in conn.execute("SELECT invoice_status, COUNT(*) as cnt FROM reconciliation_records WHERE match_status = 'invoice_only' GROUP BY invoice_status ORDER BY cnt DESC"):
    data['invoice_only_status'][r['invoice_status']] = r['cnt']

# FX examples
data['fx_examples'] = []
for r in conn.execute("""
    SELECT nvc_code, invoice_tenant, invoice_amount, payment_amount, payment_currency
    FROM reconciliation_records
    WHERE match_status = 'invoice_payment_only'
    AND ABS(COALESCE(invoice_amount,0) - COALESCE(payment_amount,0)) >= 0.02
    ORDER BY invoice_tenant
    LIMIT 15
"""):
    data['fx_examples'].append({
        'nvc_code': r['nvc_code'],
        'tenant': r['invoice_tenant'],
        'invoice_amount': r['invoice_amount'],
        'payment_amount': r['payment_amount'],
        'currency': r['payment_currency']
    })

# Overall totals
data['totals'] = {
    'total_records': sum(data['match_status'].values()),
    'total_invoiced': round(conn.execute("SELECT COALESCE(SUM(invoice_amount), 0) FROM reconciliation_records WHERE invoice_amount IS NOT NULL").fetchone()[0], 2),
    'total_paid': round(conn.execute("SELECT COALESCE(SUM(payment_amount), 0) FROM reconciliation_records WHERE payment_amount IS NOT NULL AND payment_currency = 'USD'").fetchone()[0], 2),
    'total_received': round(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM received_payments").fetchone()[0], 2),
    'total_emails': conn.execute("SELECT COUNT(DISTINCT remittance_email_id) FROM reconciliation_records WHERE remittance_email_id IS NOT NULL").fetchone()[0],
    'total_tenants': conn.execute("SELECT COUNT(DISTINCT invoice_tenant) FROM reconciliation_records WHERE invoice_tenant IS NOT NULL").fetchone()[0],
}

conn.close()
print(json.dumps(data, indent=2))
