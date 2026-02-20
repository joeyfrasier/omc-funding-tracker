"""Tests for matcher.py — reconciliation engine."""
from decimal import Decimal
from unittest.mock import patch

from csv_parser import Remittance, RemittanceLine
from matcher import reconcile, ReconciliationReport


def _make_remittance(lines, subject='Test Remittance', payment_amount=None):
    """Helper to create a Remittance with given lines."""
    if payment_amount is None:
        payment_amount = sum(l.amt_paid for l in lines)
    return Remittance(
        subject=subject,
        account_number='TEST001',
        payment_date='20260201',
        payment_amount=payment_amount,
        agency='Test Agency',
        source_type='oasys',
        lines=lines,
    )


def _make_line(nvc_code='NVC_TEST_001', amt=Decimal('1000.00'), desc='Test Contractor'):
    return RemittanceLine(
        ref_number='REF001',
        nvc_code=nvc_code,
        description=desc,
        company='Test Corp',
        inv_date='20260101',
        inv_orig_amt=amt,
        amt_paid=amt,
        disc_amt=Decimal('0'),
    )


def _make_db_record(nvc_code='NVC_TEST_001', amount=1000.00, status=4, tenant='omnicom.worksuite.com'):
    return {
        'nvc_code': nvc_code,
        'total_amount': amount,
        'status': status,
        'tenant': tenant,
        'payrun_reference': 'PR001',
    }


class TestReconcileMatching:
    """Test basic matching logic."""

    @patch('matcher.lookup_payments_by_nvc')
    def test_exact_match(self, mock_lookup):
        line = _make_line()
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record()}
        report = reconcile(_make_remittance([line]))

        assert report.matched_count == 1
        assert report.mismatched_count == 0
        assert report.not_found_count == 0
        assert report.matches[0].status == 'matched'
        assert report.matches[0].difference < 0.01

    @patch('matcher.lookup_payments_by_nvc')
    def test_amount_mismatch(self, mock_lookup):
        line = _make_line(amt=Decimal('1000.00'))
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record(amount=999.00)}
        report = reconcile(_make_remittance([line]))

        assert report.matched_count == 0
        assert report.mismatched_count == 1
        assert report.matches[0].status == 'amount_mismatch'
        assert report.matches[0].difference == 1.0

    @patch('matcher.lookup_payments_by_nvc')
    def test_within_tolerance(self, mock_lookup):
        line = _make_line(amt=Decimal('1000.00'))
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record(amount=1000.005)}
        report = reconcile(_make_remittance([line]))

        assert report.matched_count == 1
        assert report.matches[0].status == 'matched'

    @patch('matcher.lookup_payments_by_nvc')
    def test_not_in_db(self, mock_lookup):
        line = _make_line()
        mock_lookup.return_value = {}  # NVC code not found
        report = reconcile(_make_remittance([line]))

        assert report.not_found_count == 1
        assert report.matched_count == 0
        assert report.matches[0].status == 'not_in_db'
        assert report.matches[0].db_record is None

    @patch('matcher.lookup_payments_by_nvc')
    def test_empty_remittance(self, mock_lookup):
        report = reconcile(_make_remittance([]))
        mock_lookup.assert_not_called()
        assert len(report.matches) == 0


class TestStatusIssue:
    """Test rejected status override."""

    @patch('matcher.lookup_payments_by_nvc')
    def test_rejected_status_overrides_match(self, mock_lookup):
        line = _make_line()
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record(status=3)}  # Rejected
        report = reconcile(_make_remittance([line]))

        assert report.matched_count == 0  # Should NOT count as matched
        assert report.status_issue_count == 1
        assert report.matches[0].status == 'status_issue'

    @patch('matcher.lookup_payments_by_nvc')
    def test_rejected_status_overrides_mismatch(self, mock_lookup):
        line = _make_line(amt=Decimal('1000.00'))
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record(amount=999.00, status=3)}  # Rejected
        report = reconcile(_make_remittance([line]))

        assert report.mismatched_count == 0  # Should NOT count as mismatched
        assert report.status_issue_count == 1
        assert report.matches[0].status == 'status_issue'

    @patch('matcher.lookup_payments_by_nvc')
    def test_paid_status_counts_normally(self, mock_lookup):
        line = _make_line()
        mock_lookup.return_value = {'NVC_TEST_001': _make_db_record(status=2)}  # Paid
        report = reconcile(_make_remittance([line]))

        assert report.matched_count == 1
        assert report.status_issue_count == 0


class TestMultipleLines:
    """Test batch reconciliation with multiple lines."""

    @patch('matcher.lookup_payments_by_nvc')
    def test_mixed_results(self, mock_lookup):
        lines = [
            _make_line('NVC_A', Decimal('100.00')),
            _make_line('NVC_B', Decimal('200.00')),
            _make_line('NVC_C', Decimal('300.00')),
        ]
        mock_lookup.return_value = {
            'NVC_A': _make_db_record('NVC_A', 100.00),
            'NVC_B': _make_db_record('NVC_B', 999.00),
            # NVC_C not in DB
        }
        report = reconcile(_make_remittance(lines))

        assert report.matched_count == 1
        assert report.mismatched_count == 1
        assert report.not_found_count == 1
        assert len(report.matches) == 3

    @patch('matcher.lookup_payments_by_nvc')
    def test_summary_property(self, mock_lookup):
        lines = [_make_line('NVC_A', Decimal('500.00')), _make_line('NVC_B', Decimal('300.00'))]
        mock_lookup.return_value = {'NVC_A': _make_db_record('NVC_A', 500.00)}
        report = reconcile(_make_remittance(lines))

        s = report.summary
        assert s['total_lines'] == 2
        assert s['matched'] == 1
        assert s['not_found'] == 1
        assert s['agency'] == 'Test Agency'
        assert 'match_rate' in s
