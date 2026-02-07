"""Matching engine: reconcile remittance data against Worksuite pay run records."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Dict, Optional
from csv_parser import Remittance, RemittanceLine
from db_client import lookup_payments_by_nvc, status_label


@dataclass
class MatchResult:
    nvc_code: str
    remittance_line: RemittanceLine
    db_record: Optional[dict]
    status: str  # 'matched', 'amount_mismatch', 'not_in_db', 'status_issue'
    remittance_amount: Decimal
    db_amount: Optional[float]
    difference: Optional[float]
    notes: str = ''


@dataclass 
class ReconciliationReport:
    remittance: Remittance
    matches: List[MatchResult] = field(default_factory=list)
    total_remittance_amount: Decimal = Decimal('0')
    total_matched_amount: float = 0.0
    matched_count: int = 0
    mismatched_count: int = 0
    not_found_count: int = 0
    
    @property
    def summary(self):
        total = len(self.matches)
        return {
            'total_lines': total,
            'matched': self.matched_count,
            'mismatched': self.mismatched_count,
            'not_found': self.not_found_count,
            'match_rate': f"{self.matched_count/total*100:.1f}%" if total else "N/A",
            'remittance_total': str(self.remittance.payment_amount),
            'agency': self.remittance.agency,
            'account': self.remittance.account_number,
            'date': self.remittance.payment_date,
        }


def reconcile(remittance: Remittance, tolerance: Decimal = Decimal('0.01')) -> ReconciliationReport:
    """Match a remittance against the database.
    
    Args:
        remittance: Parsed remittance data
        tolerance: Amount tolerance for matching (default $0.01)
    
    Returns:
        ReconciliationReport with match details
    """
    report = ReconciliationReport(remittance=remittance)
    report.total_remittance_amount = remittance.payment_amount
    
    if not remittance.lines:
        return report
    
    # Batch lookup all NVC codes
    nvc_codes = [line.nvc_code for line in remittance.lines]
    db_records = lookup_payments_by_nvc(nvc_codes)
    
    for line in remittance.lines:
        db_rec = db_records.get(line.nvc_code)
        
        if not db_rec:
            result = MatchResult(
                nvc_code=line.nvc_code,
                remittance_line=line,
                db_record=None,
                status='not_in_db',
                remittance_amount=line.amt_paid,
                db_amount=None,
                difference=None,
                notes=f'NVC code {line.nvc_code} not found in database',
            )
            report.not_found_count += 1
        else:
            db_amount = db_rec.get('total_amount', 0)
            diff = float(line.amt_paid) - (db_amount or 0)
            
            if abs(diff) <= float(tolerance):
                status = 'matched'
                report.matched_count += 1
                report.total_matched_amount += db_amount or 0
                notes = f'✓ Matched (DB status: {status_label(db_rec.get("status", -1))})'
            else:
                status = 'amount_mismatch'
                report.mismatched_count += 1
                notes = f'Amount mismatch: remit=${line.amt_paid} vs db=${db_amount:.2f} (diff=${diff:+.2f})'
            
            # Check for concerning statuses
            db_status = db_rec.get('status', -1)
            if db_status in (5, 6):  # Rejected or Cancelled
                status = 'status_issue'
                notes += f' ⚠️ Payment is {status_label(db_status)}!'
            
            result = MatchResult(
                nvc_code=line.nvc_code,
                remittance_line=line,
                db_record=db_rec,
                status=status,
                remittance_amount=line.amt_paid,
                db_amount=db_amount,
                difference=diff,
                notes=notes,
            )
        
        report.matches.append(result)
    
    return report


def reconcile_batch(remittances: List[Remittance]) -> List[ReconciliationReport]:
    """Reconcile multiple remittances."""
    reports = []
    for r in remittances:
        try:
            report = reconcile(r)
            reports.append(report)
        except Exception as e:
            print(f"  Error reconciling {r.subject}: {e}")
    return reports


def print_report(report: ReconciliationReport):
    """Print a human-readable report."""
    s = report.summary
    print(f"\n{'='*80}")
    print(f"Remittance: {report.remittance.subject}")
    print(f"Agency: {s['agency'] or 'Unknown'} | Account: {s['account']} | Date: {s['date']}")
    print(f"Total: ${s['remittance_total']} | Lines: {s['total_lines']}")
    print(f"Matched: {s['matched']} | Mismatched: {s['mismatched']} | Not Found: {s['not_found']} | Rate: {s['match_rate']}")
    print(f"{'-'*80}")
    
    for m in report.matches:
        icon = {'matched': '✅', 'amount_mismatch': '⚠️', 'not_in_db': '❌', 'status_issue': '🚨'}.get(m.status, '?')
        tenant = m.db_record.get('tenant', '').replace('.worksuite.com', '') if m.db_record else ''
        print(f"  {icon} {m.nvc_code} | {m.remittance_line.description:30} | ${m.remittance_amount:>10} | {m.notes} | {tenant}")


if __name__ == '__main__':
    from csv_parser import parse_csv
    
    sample = b"""Account Number: V00121139
Payment date: 20260208
Payment Amount : 26,872.70
Ref Number\tInv Nbr\tInvoice description\tCompany Statement Name\tInv Date\tInv Orig Amt\tAmt Pd\tDisc Amt
OMPS-PR0005742\tNVC7KTPCPVVV\tCat Ventura\tOmni Prod. LLC\t20260129\t600.00\t600.00\t0.00
OMPS-PR0005742\tNVC7KY46WXLW\tChris James Champeau\tOmni Prod. LLC\t20260202\t14,272.70\t14,272.70\t0.00
OMPS-PR0005742\tNVC7KVC7X37T\tChristopher Hall\tOmni Prod. LLC\t20260130\t12,000.00\t12,000.00\t0.00"""
    
    r = parse_csv(sample, subject='On behalf of OGI Shared Service Center Advertising LLC')
    if r:
        report = reconcile(r)
        print_report(report)
