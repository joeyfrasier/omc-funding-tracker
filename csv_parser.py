"""Parser for Omnicom remittance CSV files (OASYS and D365 ACH format)."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


@dataclass
class RemittanceLine:
    ref_number: str
    nvc_code: str  # Inv Nbr / NVC code
    description: str  # Invoice description / contractor name
    company: str  # Company Statement Name
    inv_date: str
    inv_orig_amt: Decimal
    amt_paid: Decimal
    disc_amt: Decimal


@dataclass
class Remittance:
    account_number: str
    payment_date: str  # YYYYMMDD
    payment_amount: Decimal
    lines: List[RemittanceLine] = field(default_factory=list)
    source_email_id: str = ''
    source_type: str = ''  # oasys, d365_ach
    subject: str = ''
    agency: str = ''  # Extracted from subject


def parse_amount(s: str) -> Decimal:
    """Parse amount string like '26,872.70' to Decimal."""
    s = s.strip().replace(',', '')
    if not s or s == '-':
        return Decimal('0')
    return Decimal(s)


def parse_csv(data: bytes, source_type: str = 'oasys', email_id: str = '', subject: str = '') -> Optional[Remittance]:
    """Parse a Remittance.csv file (works for both OASYS and D365 ACH format).
    
    Format:
        Account Number: V00121139
        Payment date: 20260208
        Payment Amount : 26,872.70
        Ref Number\tInv Nbr\t...
        LINE1\tNVC...\t...
    """
    # Detect encoding - UTF-16 LE BOM is common for OASYS CSVs
    if data[:2] == b'\xff\xfe':
        text = data.decode('utf-16-le')
    elif data[:2] == b'\xfe\xff':
        text = data.decode('utf-16-be')
    elif data[:3] == b'\xef\xbb\xbf':
        text = data.decode('utf-8')
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('latin-1')
    
    # Strip BOM chars
    text = text.lstrip('\ufeff\xfeff')
    
    lines = text.strip().split('\n')
    
    account_number = ''
    payment_date = ''
    payment_amount = Decimal('0')
    remittance_lines = []
    header_found = False
    
    for line in lines:
        line = line.strip('\r\n\t ')
        
        if not line:
            continue
        
        # Parse header fields
        if line.startswith('Account Number:'):
            account_number = line.split(':', 1)[1].strip()
            continue
        if line.startswith('Payment date:'):
            payment_date = line.split(':', 1)[1].strip()
            continue
        if line.startswith('Payment Amount'):
            payment_amount = parse_amount(line.split(':', 1)[1].strip())
            continue
        
        # Detect header row
        if 'Ref Number' in line and 'Inv Nbr' in line:
            header_found = True
            continue
        
        # Parse data rows (tab-delimited)
        if header_found:
            parts = line.split('\t')
            if len(parts) >= 7:
                try:
                    rl = RemittanceLine(
                        ref_number=parts[0].strip(),
                        nvc_code=parts[1].strip(),
                        description=parts[2].strip(),
                        company=parts[3].strip(),
                        inv_date=parts[4].strip(),
                        inv_orig_amt=parse_amount(parts[5]),
                        amt_paid=parse_amount(parts[6]),
                        disc_amt=parse_amount(parts[7]) if len(parts) > 7 else Decimal('0'),
                    )
                    if rl.nvc_code:  # Only add lines with NVC codes
                        remittance_lines.append(rl)
                except Exception:
                    continue
    
    if not account_number and not remittance_lines:
        return None
    
    # Extract agency from subject
    agency = ''
    if subject:
        m = re.search(r'On behalf of (.+)', subject)
        if m:
            agency = m.group(1).strip()
    
    return Remittance(
        account_number=account_number,
        payment_date=payment_date,
        payment_amount=payment_amount,
        lines=remittance_lines,
        source_email_id=email_id,
        source_type=source_type,
        subject=subject,
        agency=agency,
    )


def parse_email_attachments(email_data: dict) -> List[Remittance]:
    """Parse all CSV attachments from an email dict (from gmail_client)."""
    remittances = []
    for att in email_data.get('attachments', []):
        fn = att['filename'].lower()
        if fn.endswith('.csv'):
            r = parse_csv(
                att['data'],
                source_type=email_data.get('source', 'unknown'),
                email_id=email_data.get('id', ''),
                subject=email_data.get('subject', ''),
            )
            if r:
                remittances.append(r)
    return remittances


if __name__ == '__main__':
    # Test with sample data
    sample = b"""Account Number: V00121139
Payment date: 20260208
Payment Amount : 26,872.70
Ref Number\tInv Nbr\tInvoice description\tCompany Statement Name\tInv Date\tInv Orig Amt\tAmt Pd\tDisc Amt
OMPS-PR0005742\tNVC7KTPCPVVV\tCat Ventura\tOmni Prod. LLC\t20260129\t600.00\t600.00\t0.00
OMPS-PR0005742\tNVC7KY46WXLW\tChris James Champeau\tOmni Prod. LLC\t20260202\t14,272.70\t14,272.70\t0.00
OMPS-PR0005742\tNVC7KVC7X37T\tChristopher Hall\tOmni Prod. LLC\t20260130\t12,000.00\t12,000.00\t0.00"""
    
    r = parse_csv(sample, subject='On behalf of OGI Shared Service Center Advertising LLC')
    if r:
        print(f"Account: {r.account_number}")
        print(f"Date: {r.payment_date}")
        print(f"Amount: {r.payment_amount}")
        print(f"Agency: {r.agency}")
        print(f"Lines: {len(r.lines)}")
        for l in r.lines:
            print(f"  {l.nvc_code} | {l.description:30} | ${l.amt_paid}")
