"""Tests for CSV parser."""
import sys
sys.path.insert(0, '..')
from decimal import Decimal
from csv_parser import parse_csv, parse_amount


def test_parse_amount():
    assert parse_amount('26,872.70') == Decimal('26872.70')
    assert parse_amount('600.00') == Decimal('600.00')
    assert parse_amount('0.00') == Decimal('0')
    assert parse_amount('') == Decimal('0')
    assert parse_amount('-') == Decimal('0')


def test_parse_oasys_csv():
    data = b"""Account Number: V00121139
Payment date: 20260208
Payment Amount : 26,872.70
Ref Number\tInv Nbr\tInvoice description\tCompany Statement Name\tInv Date\tInv Orig Amt\tAmt Pd\tDisc Amt
OMPS-PR0005742\tNVC7KTPCPVVV\tCat Ventura\tOmni Prod. LLC\t20260129\t600.00\t600.00\t0.00
OMPS-PR0005742\tNVC7KY46WXLW\tChris James Champeau\tOmni Prod. LLC\t20260202\t14,272.70\t14,272.70\t0.00
OMPS-PR0005742\tNVC7KVC7X37T\tChristopher Hall\tOmni Prod. LLC\t20260130\t12,000.00\t12,000.00\t0.00"""
    
    r = parse_csv(data, subject='On behalf of OGI Shared Service Center Advertising LLC')
    assert r is not None
    assert r.account_number == 'V00121139'
    assert r.payment_date == '20260208'
    assert r.payment_amount == Decimal('26872.70')
    assert r.agency == 'OGI Shared Service Center Advertising LLC'
    assert len(r.lines) == 3
    assert r.lines[0].nvc_code == 'NVC7KTPCPVVV'
    assert r.lines[0].description == 'Cat Ventura'
    assert r.lines[0].amt_paid == Decimal('600.00')
    assert r.lines[1].amt_paid == Decimal('14272.70')


def test_parse_d365_csv():
    data = b"""Account Number: MOCOUS
Payment date: 20260208
Payment Amount : 500.00
Ref Number\tInv Nbr\tInvoice description\tCompany Statement Name\tInv Date\tInv Orig Amt\tAmt Pd\tDisc Amt
6130-PR0000902\tNVC7L6JMH3W9\tJUMP 450 WENDY BROICH-UGC CREATOR 2026 JANUARY\tJump450\t20260205\t500.00\t500.00\t0.00"""
    
    r = parse_csv(data, source_type='d365_ach')
    assert r is not None
    assert r.account_number == 'MOCOUS'
    assert len(r.lines) == 1
    assert r.lines[0].nvc_code == 'NVC7L6JMH3W9'


def test_parse_with_bom():
    data = b'\xef\xbb\xbfAccount Number: TEST\nPayment date: 20260101\nPayment Amount : 100.00\n'
    r = parse_csv(data)
    assert r is not None
    assert r.account_number == 'TEST'


if __name__ == '__main__':
    test_parse_amount()
    test_parse_oasys_csv()
    test_parse_d365_csv()
    test_parse_with_bom()
    print("All CSV parser tests passed!")
