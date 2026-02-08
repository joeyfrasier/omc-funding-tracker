"""Parse remittance CSVs from OASYS and D365 ACH emails.

Extracts: NVC codes, amounts, agency names, pay run references.
"""

import base64
import csv
import io
import re
from datetime import datetime
from typing import Optional

from gmail_client import get_service


def parse_oasys_csv(csv_text: str) -> dict:
    """Parse OASYS Remittance.csv format.
    
    Header format:
        Account Number: V00121139
        Payment date: 20260208
        Payment Amount : 26,872.70
        Ref Number\tInv Nbr\tInvoice description\tCompany Statement Name\tInv Date\tInv Orig Amt\tAmt Pd\tDisc Amt
        OMPS-PR0005742\tNVC7KTPCPVVV\t...
    """
    lines = csv_text.strip().split('\n')
    
    # Parse header metadata
    account_number = None
    payment_date = None
    payment_amount = None
    
    data_start = 0
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith('\ufeff'):
            line = line[1:]
        
        if line.startswith('Account Number:'):
            account_number = line.split(':', 1)[1].strip()
        elif line.startswith('Payment date:'):
            raw = line.split(':', 1)[1].strip()
            try:
                payment_date = datetime.strptime(raw, '%Y%m%d').strftime('%Y-%m-%d')
            except ValueError:
                payment_date = raw
        elif line.startswith('Payment Amount'):
            raw = line.split(':', 1)[1].strip()
            payment_amount = float(raw.replace(',', ''))
        elif line.startswith('Ref Number'):
            data_start = i + 1
            break
    
    # Parse line items
    items = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            try:
                items.append({
                    'pay_run_ref': parts[0].strip(),
                    'nvc_code': parts[1].strip(),
                    'contractor_name': parts[2].strip(),
                    'company': parts[3].strip(),
                    'invoice_date': parts[4].strip(),
                    'original_amount': float(parts[5].replace(',', '')) if parts[5].strip() else 0,
                    'amount_paid': float(parts[6].replace(',', '')) if parts[6].strip() else 0,
                    'discount': float(parts[7].replace(',', '')) if len(parts) > 7 and parts[7].strip() else 0,
                })
            except (ValueError, IndexError):
                continue
    
    return {
        'source': 'oasys',
        'account_number': account_number,
        'payment_date': payment_date,
        'payment_amount': payment_amount,
        'item_count': len(items),
        'items_total': sum(i['amount_paid'] for i in items),
        'items': items,
    }


def parse_d365_csv(csv_text: str) -> dict:
    """Parse D365 ACH Remittance format (needs sample to refine)."""
    # Normalize line endings
    csv_text = csv_text.replace('\r\n', '\n').replace('\r', '\n')
    reader = csv.DictReader(io.StringIO(csv_text))
    items = []
    try:
        for row in reader:
            items.append(dict(row))
    except csv.Error:
        # Fallback: just split by lines
        lines = csv_text.strip().split('\n')
        for line in lines[:5]:
            items.append({'raw': line[:200]})
    
    return {
        'source': 'd365_ach',
        'item_count': len(items),
        'items': items,
        'raw_columns': list(items[0].keys()) if items else [],
    }


def fetch_and_parse_remittances(days_back: int = 90, max_emails: int = 50) -> list[dict]:
    """Fetch remittance emails and parse their CSV attachments."""
    svc = get_service()
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days_back)
    cutoff_str = cutoff.strftime('%Y/%m/%d')
    
    parsed = []
    
    # OASYS emails
    query = f'omcoasys has:attachment after:{cutoff_str}'
    results = svc.users().messages().list(userId='me', q=query, maxResults=max_emails).execute()
    oasys_msgs = results.get('messages', [])
    
    for msg_meta in oasys_msgs:
        msg = svc.users().messages().get(userId='me', id=msg_meta['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in msg['payload']['headers']}
        
        # Find CSV attachment
        csv_data = _extract_csv_attachment(svc, msg_meta['id'], msg['payload'])
        if csv_data:
            remittance = parse_oasys_csv(csv_data)
            remittance['email_id'] = msg_meta['id']
            remittance['email_subject'] = headers.get('Subject', '')
            remittance['email_date'] = headers.get('Date', '')
            remittance['email_from'] = headers.get('From', '')
            parsed.append(remittance)
    
    # D365 ACH emails
    query = f'subject:"OMG AP ACH PAYMENT REMITTANCE" has:attachment after:{cutoff_str}'
    results = svc.users().messages().list(userId='me', q=query, maxResults=max_emails).execute()
    d365_msgs = results.get('messages', [])
    
    for msg_meta in d365_msgs:
        msg = svc.users().messages().get(userId='me', id=msg_meta['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in msg['payload']['headers']}
        
        csv_data = _extract_csv_attachment(svc, msg_meta['id'], msg['payload'])
        if csv_data:
            remittance = parse_d365_csv(csv_data)
            remittance['email_id'] = msg_meta['id']
            remittance['email_subject'] = headers.get('Subject', '')
            remittance['email_date'] = headers.get('Date', '')
            remittance['email_from'] = headers.get('From', '')
            parsed.append(remittance)
    
    return parsed


def _extract_csv_attachment(svc, msg_id: str, payload: dict) -> Optional[str]:
    """Recursively find and extract CSV attachment from email payload."""
    parts = payload.get('parts', [])
    
    # Direct attachment
    if payload.get('filename') and payload['filename'].lower().endswith('.csv'):
        att_id = payload['body'].get('attachmentId')
        if att_id:
            att = svc.users().messages().attachments().get(userId='me', messageId=msg_id, id=att_id).execute()
            raw = base64.urlsafe_b64decode(att['data'])
            # Detect encoding: UTF-16LE BOM = \xff\xfe
            if raw[:2] == b'\xff\xfe':
                return raw.decode('utf-16-le', errors='replace')
            elif raw[:2] == b'\xfe\xff':
                return raw.decode('utf-16-be', errors='replace')
            return raw.decode('utf-8', errors='replace')
    
    # Search parts
    for part in parts:
        result = _extract_csv_attachment(svc, msg_id, part)
        if result:
            return result
    
    return None


if __name__ == '__main__':
    print("Fetching and parsing remittance CSVs...")
    remittances = fetch_and_parse_remittances(days_back=90)
    
    print(f"\nParsed {len(remittances)} remittance emails\n")
    
    all_nvc_codes = []
    for r in remittances:
        print(f"{'='*60}")
        print(f"Source: {r['source']} | Date: {r.get('payment_date', r.get('email_date',''))}")
        print(f"Subject: {r.get('email_subject','')[:80]}")
        if r['source'] == 'oasys':
            print(f"Account: {r.get('account_number')} | Amount: ${r.get('payment_amount') or 0:,.2f}")
            print(f"Items: {r['item_count']} | Items Total: ${r.get('items_total',0):,.2f}")
            for item in r.get('items', [])[:5]:
                print(f"  {item['nvc_code']:15} | {item['contractor_name']:30} | ${item['amount_paid']:>10,.2f}")
                all_nvc_codes.append(item['nvc_code'])
            if r['item_count'] > 5:
                print(f"  ... and {r['item_count'] - 5} more items")
        elif r['source'] == 'd365_ach':
            print(f"Items: {r['item_count']} | Columns: {r.get('raw_columns', [])}")
    
    print(f"\n{'='*60}")
    print(f"Total NVC codes extracted: {len(all_nvc_codes)}")
    print(f"Unique NVC codes: {len(set(all_nvc_codes))}")
