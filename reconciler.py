"""OMC Funding Reconciliation Engine.

Triangulates three data sources:
1. Gmail remittances (what agencies say they're paying)
2. Worksuite DB invoices (what we're owed / what we paid contractors)
3. MoneyCorp payments (actual money in/out of sub-accounts)
"""

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from db_client import get_connection
from gmail_client import get_service, EMAIL_SOURCES
from moneycorp_client import authenticate

load_dotenv()

# Data directory
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

# OMC Tenant → MoneyCorp mapping
TENANT_MONEYCORP_MAP = {
    'omnicomtbwa.worksuite.com': {'mc_id': '859156', 'agency': 'TBWA'},
    'omnicomddb.worksuite.com': {'mc_id': '859154', 'agency': 'DDB'},
    'omcbbdo.worksuite.com': {'mc_id': '859152', 'agency': 'BBDO'},
    'omnicomprecision.worksuite.com': {'mc_id': '859147', 'agency': 'Precision Marketing'},
    'omcflywheel.worksuite.com': {'mc_id': '859146', 'agency': 'Flywheel Digital'},
    'omnicomoac.worksuite.com': {'mc_id': '859140', 'agency': 'OAC'},
    'omnicommedia.worksuite.com': {'mc_id': '859138', 'agency': 'Media'},
    'omnicomprg.worksuite.com': {'mc_id': '859136', 'agency': 'Public Relations'},
    'omnicombranding.worksuite.com': {'mc_id': '859135', 'agency': 'Branding Consulting'},
    'omcohg.worksuite.com': {'mc_id': '859133', 'agency': 'Healthcare'},
    'omnicom.worksuite.com': {'mc_id': '859149', 'agency': 'Specialty Marketing'},
}

# Import canonical payment status codes from db_client
from db_client import PAYMENT_STATUS, status_label


def fetch_invoices(days_back: int = 90) -> list[dict]:
    """Fetch all OMC invoices from Worksuite aggregate DB."""
    tenants = list(TENANT_MONEYCORP_MAP.keys())
    placeholders = ','.join(['%s'] * len(tenants))
    cutoff = datetime.now() - timedelta(days=days_back)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT tenant, id, number, invoice_id, total_amount, currency,
                   funding_currency, status, payment_method,
                   created_at, approved_date, processing_date,
                   paid_date, paid_day, payrun_id,
                   worker_id, vendor_id, contract_id
            FROM documents_payment
            WHERE tenant IN ({placeholders})
              AND created_at >= %s
            ORDER BY tenant, created_at DESC
        """, tenants + [cutoff])

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    invoices = []
    for row in rows:
        inv = dict(zip(columns, row))
        inv['total_amount'] = float(inv['total_amount']) if inv['total_amount'] else 0
        inv['status_label'] = status_label(inv['status'])
        inv['agency'] = TENANT_MONEYCORP_MAP.get(inv['tenant'], {}).get('agency', 'Unknown')
        inv['mc_account_id'] = TENANT_MONEYCORP_MAP.get(inv['tenant'], {}).get('mc_id')
        # Convert datetimes to strings for JSON
        for key in ['created_at', 'approved_date', 'processing_date', 'paid_date', 'paid_day']:
            if inv[key]:
                inv[key] = str(inv[key])
        invoices.append(inv)

    return invoices


def fetch_moneycorp_received(account_id: str) -> list[dict]:
    """Fetch received payments (incoming funding) for a MoneyCorp sub-account."""
    token = authenticate()
    headers = {'Authorization': f'Bearer {token}'}

    resp = requests.get(
        f'https://corpapi.moneycorp.com/accounts/{account_id}/receivedPayments',
        headers=headers, timeout=30
    )
    resp.raise_for_status()

    payments = []
    for p in resp.json().get('data', []):
        attrs = p['attributes']
        payments.append({
            'mc_payment_id': p['id'],
            'type': 'received',
            'status': attrs.get('paymentStatus'),
            'currency': attrs.get('currency'),
            'amount': float(attrs.get('amount', 0)),
            'date': attrs.get('paymentDate'),
            'reference': attrs.get('reference'),
            'msl_ref1': attrs.get('mslReference1'),
            'msl_ref2': attrs.get('mslReference2'),
            'created_by': attrs.get('createdBy'),
        })
    return payments


def fetch_moneycorp_outgoing(account_id: str) -> list[dict]:
    """Fetch outgoing payments (contractor payouts) for a MoneyCorp sub-account."""
    token = authenticate()
    headers = {'Authorization': f'Bearer {token}'}

    resp = requests.get(
        f'https://corpapi.moneycorp.com/accounts/{account_id}/payments',
        headers=headers, timeout=30
    )
    resp.raise_for_status()

    payments = []
    for p in resp.json().get('data', []):
        attrs = p['attributes']
        recipient = attrs.get('recipientDetails', {})
        payments.append({
            'mc_payment_id': p['id'],
            'type': 'outgoing',
            'status': attrs.get('paymentStatus'),
            'currency': attrs.get('paymentCurrency'),
            'amount': float(attrs.get('paymentAmount', 0)),
            'date': attrs.get('paymentDate'),
            'value_date': attrs.get('paymentValueDate'),
            'approved': attrs.get('paymentApproved'),
            'recipient_name': recipient.get('bankAccountName'),
            'recipient_country': recipient.get('bankAccountCountry'),
        })
    return payments


def fetch_moneycorp_balance(account_id: str) -> list[dict]:
    """Fetch current balance for a MoneyCorp sub-account."""
    token = authenticate()
    headers = {'Authorization': f'Bearer {token}'}

    resp = requests.get(
        f'https://corpapi.moneycorp.com/accounts/{account_id}/balances',
        headers=headers, timeout=30
    )
    resp.raise_for_status()

    balances = []
    for b in resp.json().get('data', []):
        attrs = b['attributes']
        balances.append({
            'currency': attrs.get('currencyCode'),
            'overall': float(attrs.get('overallBalance', 0)),
            'available': float(attrs.get('availableBalance', 0)),
            'cleared': float(attrs.get('clearedBalance', 0)),
            'reserved': float(attrs.get('reservedBalance', 0)),
        })
    return balances


def fetch_remittance_emails(days_back: int = 90) -> list[dict]:
    """Fetch remittance emails from Gmail."""
    svc = get_service()
    cutoff = datetime.now() - timedelta(days=days_back)
    cutoff_str = cutoff.strftime('%Y/%m/%d')

    remittances = []
    for source_key, source_config in EMAIL_SOURCES.items():
        query = f"{source_config['query']} after:{cutoff_str}"
        results = svc.users().messages().list(userId='me', q=query, maxResults=100).execute()
        messages = results.get('messages', [])

        for msg_meta in messages:
            msg = svc.users().messages().get(userId='me', id=msg_meta['id'], format='metadata',
                                              metadataHeaders=['From', 'Subject', 'Date']).execute()
            headers = {h['name']: h['value'] for h in msg['payload']['headers']}
            remittances.append({
                'source': source_key,
                'message_id': msg_meta['id'],
                'from': headers.get('From', ''),
                'subject': headers.get('Subject', ''),
                'date': headers.get('Date', ''),
                'description': source_config['description'],
            })

    return remittances


def build_reconciliation_report(days_back: int = 90) -> dict:
    """Build full reconciliation report across all three data sources."""
    print("📊 Building OMC Funding Reconciliation Report...")
    print(f"   Looking back {days_back} days\n")

    # 1. Fetch invoices from Worksuite
    print("1️⃣  Fetching invoices from Worksuite DB...")
    invoices = fetch_invoices(days_back)
    print(f"   Found {len(invoices)} invoices across {len(TENANT_MONEYCORP_MAP)} agencies\n")

    # 2. Fetch MoneyCorp data for each agency
    print("2️⃣  Fetching MoneyCorp payment data...")
    mc_data = {}
    for tenant, config in TENANT_MONEYCORP_MAP.items():
        mc_id = config['mc_id']
        agency = config['agency']
        try:
            received = fetch_moneycorp_received(mc_id)
            outgoing = fetch_moneycorp_outgoing(mc_id)
            balances = fetch_moneycorp_balance(mc_id)
            mc_data[tenant] = {
                'agency': agency,
                'mc_id': mc_id,
                'received': received,
                'outgoing': outgoing,
                'balances': balances,
                'total_received': sum(p['amount'] for p in received),
                'total_outgoing': sum(p['amount'] for p in outgoing),
            }
            print(f"   {agency:30} | Received: {len(received):3} (${mc_data[tenant]['total_received']:>12,.2f}) | "
                  f"Outgoing: {len(outgoing):3} (${mc_data[tenant]['total_outgoing']:>12,.2f}) | "
                  f"Balance: ${balances[0]['available']:>10,.2f}" if balances else f"   {agency:30} | No balances")
        except Exception as e:
            print(f"   {agency:30} | ERROR: {e}")
            mc_data[tenant] = {'agency': agency, 'mc_id': mc_id, 'received': [], 'outgoing': [], 'balances': [], 'error': str(e)}

    # 3. Fetch remittance emails
    print(f"\n3️⃣  Fetching remittance emails from Gmail...")
    remittances = fetch_remittance_emails(days_back)
    print(f"   Found {len(remittances)} remittance emails\n")

    # 4. Build per-agency summary
    print("4️⃣  Building per-agency reconciliation...\n")
    agency_summaries = {}
    for tenant, config in TENANT_MONEYCORP_MAP.items():
        agency = config['agency']
        tenant_invoices = [i for i in invoices if i['tenant'] == tenant]
        mc = mc_data.get(tenant, {})

        total_invoiced = sum(i['total_amount'] for i in tenant_invoices)
        total_paid = sum(i['total_amount'] for i in tenant_invoices if i['status'] == 4)       # Paid
        total_approved = sum(i['total_amount'] for i in tenant_invoices if i['status'] == 1)  # Approved
        total_processing = sum(i['total_amount'] for i in tenant_invoices if i['status'] == 2)  # Processing

        summary = {
            'agency': agency,
            'tenant': tenant,
            'mc_id': config['mc_id'],
            'invoice_count': len(tenant_invoices),
            'total_invoiced': total_invoiced,
            'total_paid': total_paid,
            'total_approved': total_approved,
            'total_processing': total_processing,
            'mc_received_count': len(mc.get('received', [])),
            'mc_received_total': mc.get('total_received', 0),
            'mc_outgoing_count': len(mc.get('outgoing', [])),
            'mc_outgoing_total': mc.get('total_outgoing', 0),
            'mc_balance': mc.get('balances', [{}])[0].get('available', 0) if mc.get('balances') else 0,
            'funding_gap': total_approved + total_processing - mc.get('total_received', 0),
        }
        agency_summaries[tenant] = summary

        print(f"   {agency:30}")
        print(f"     Invoices: {summary['invoice_count']:>5}  Total: ${total_invoiced:>12,.2f}")
        print(f"     Paid:     ${total_paid:>12,.2f}  Approved: ${total_approved:>12,.2f}  Processing: ${total_processing:>12,.2f}")
        print(f"     MC In:    ${summary['mc_received_total']:>12,.2f}  MC Out: ${summary['mc_outgoing_total']:>12,.2f}  Balance: ${summary['mc_balance']:>10,.2f}")
        if summary['funding_gap'] > 0:
            print(f"     ⚠️  Funding Gap: ${summary['funding_gap']:>12,.2f}")
        print()

    # 5. Build report
    report = {
        'generated_at': datetime.now().isoformat(),
        'days_back': days_back,
        'summary': {
            'total_invoices': len(invoices),
            'total_invoiced': sum(s['total_invoiced'] for s in agency_summaries.values()),
            'total_paid': sum(s['total_paid'] for s in agency_summaries.values()),
            'total_mc_received': sum(s['mc_received_total'] for s in agency_summaries.values()),
            'total_mc_outgoing': sum(s['mc_outgoing_total'] for s in agency_summaries.values()),
            'total_remittance_emails': len(remittances),
        },
        'agencies': agency_summaries,
        'invoices': invoices,
        'moneycorp': {t: {k: v for k, v in d.items() if k != 'error'} for t, d in mc_data.items()},
        'remittances': remittances,
    }

    # Save report
    report_path = DATA_DIR / f"reconciliation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"📄 Report saved to {report_path}")

    return report


if __name__ == '__main__':
    report = build_reconciliation_report(days_back=90)
    print("\n" + "=" * 80)
    print("RECONCILIATION SUMMARY")
    print("=" * 80)
    s = report['summary']
    print(f"Total Invoices:        {s['total_invoices']:>8}")
    print(f"Total Invoiced:        ${s['total_invoiced']:>12,.2f}")
    print(f"Total Paid:            ${s['total_paid']:>12,.2f}")
    print(f"MC Received (funding): ${s['total_mc_received']:>12,.2f}")
    print(f"MC Outgoing (payouts): ${s['total_mc_outgoing']:>12,.2f}")
    print(f"Remittance Emails:     {s['total_remittance_emails']:>8}")
