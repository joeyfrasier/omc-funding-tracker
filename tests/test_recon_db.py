"""Tests for recon_db.py helper functions."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary recon DB for testing."""
    db_path = tmp_path / 'test_recon.db'
    with patch('recon_db.RECON_DB_PATH', db_path):
        import recon_db
        recon_db.init_recon_db()
        recon_db._migrate_add_flag_columns()
        recon_db._migrate_4way_columns()
        yield recon_db, db_path


class TestUpsertAndStatus:
    """Test upsert operations and status calculation."""

    def test_upsert_remittance_creates_record(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_TEST_1', 1000.0, '2026-02-01', 'oasys', 'email_123')
        record = db.get_recon_record('NVC_TEST_1')
        assert record is not None
        assert record['remittance_amount'] == 1000.0
        assert record['match_status'] == 'remittance_only'

    def test_upsert_invoice_creates_record(self, temp_db):
        db, _ = temp_db
        db.upsert_from_invoice('NVC_TEST_2', 500.0, 'Paid', 'omnicom', 'PR001', 'USD')
        record = db.get_recon_record('NVC_TEST_2')
        assert record is not None
        assert record['invoice_amount'] == 500.0
        assert record['match_status'] == 'invoice_only'

    def test_two_way_match(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_2WAY', 1000.0, '2026-02-01', 'oasys', 'email_1')
        db.upsert_from_invoice('NVC_2WAY', 1000.0, 'Paid', 'omnicom', 'PR001', 'USD')
        record = db.get_recon_record('NVC_2WAY')
        assert record['match_status'] == '2way_matched'

    def test_amount_mismatch(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_MM', 1000.0, '2026-02-01', 'oasys', 'email_1')
        db.upsert_from_invoice('NVC_MM', 999.0, 'Paid', 'omnicom', 'PR001', 'USD')
        record = db.get_recon_record('NVC_MM')
        assert record['match_status'] == 'amount_mismatch'

    def test_within_tolerance_matches(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_TOL', 1000.0, '2026-02-01', 'oasys', 'email_1')
        db.upsert_from_invoice('NVC_TOL', 1000.005, 'Paid', 'omnicom', 'PR001', 'USD')
        record = db.get_recon_record('NVC_TOL')
        assert record['match_status'] == '2way_matched'


class TestHelperFunctions:
    """Test the new helper functions added during refactor."""

    def test_get_agency_stats(self, temp_db):
        db, _ = temp_db
        db.upsert_from_invoice('NVC_A', 100.0, 'Paid', 'tenant_a', 'PR001', 'USD')
        db.upsert_from_invoice('NVC_B', 200.0, 'Paid', 'tenant_a', 'PR002', 'USD')
        db.upsert_from_invoice('NVC_C', 5000.0, 'Paid', 'tenant_b', 'PR003', 'USD')

        stats = db.get_agency_stats()
        assert len(stats) == 2
        # tenant_b has higher total_value (5000 vs 300), should be first
        assert stats[0]['name'] == 'tenant_b'
        assert stats[0]['count'] == 1
        assert stats[1]['name'] == 'tenant_a'
        assert stats[1]['count'] == 2

    def test_get_nvc_codes_for_email(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_E1', 100.0, '2026-01-01', 'oasys', 'email_X')
        db.upsert_from_remittance('NVC_E2', 200.0, '2026-01-01', 'oasys', 'email_X')
        db.upsert_from_remittance('NVC_E3', 300.0, '2026-01-01', 'oasys', 'email_Y')

        codes = db.get_nvc_codes_for_email('email_X')
        assert set(codes) == {'NVC_E1', 'NVC_E2'}

    def test_get_email_remittance_totals(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_T1', 100.0, '2026-01-01', 'oasys', 'email_A')
        db.upsert_from_remittance('NVC_T2', 200.0, '2026-01-02', 'oasys', 'email_A')
        db.upsert_from_remittance('NVC_T3', 500.0, '2026-01-03', 'oasys', 'email_B')

        totals = db.get_email_remittance_totals()
        by_email = {t['remittance_email_id']: t for t in totals}
        assert by_email['email_A']['total_amount'] == 300.0
        assert by_email['email_A']['nvc_count'] == 2
        assert by_email['email_B']['total_amount'] == 500.0

    def test_update_recon_flag(self, temp_db):
        db, _ = temp_db
        db.upsert_from_invoice('NVC_FLAG', 100.0, 'Paid', 'tenant', 'PR001', 'USD')
        db.update_recon_flag('NVC_FLAG', 'investigating', 'checking amount')
        record = db.get_recon_record('NVC_FLAG')
        assert record['flag'] == 'investigating'
        assert record['flag_notes'] == 'checking amount'

    def test_append_recon_note(self, temp_db):
        db, _ = temp_db
        db.upsert_from_invoice('NVC_NOTE', 100.0, 'Paid', 'tenant', 'PR001', 'USD')
        db.append_recon_note('NVC_NOTE', 'First note')
        db.append_recon_note('NVC_NOTE', 'Second note')
        record = db.get_recon_record('NVC_NOTE')
        assert 'First note' in record['notes']
        assert 'Second note' in record['notes']

    def test_search_recon_records(self, temp_db):
        db, _ = temp_db
        db.upsert_from_invoice('NVC_S1', 100.0, 'Paid', 'tenant_a', 'PR001', 'USD')
        db.upsert_from_invoice('NVC_S2', 500.0, 'Paid', 'tenant_a', 'PR002', 'USD')
        db.upsert_from_invoice('NVC_S3', 1000.0, 'Paid', 'tenant_b', 'PR003', 'USD')

        # Search by amount range
        results = db.search_recon_records('invoice_amount', amount_min=200, amount_max=600)
        assert len(results) == 1
        assert results[0]['nvc_code'] == 'NVC_S2'

        # Search by tenant
        results = db.search_recon_records('invoice_amount', tenant='tenant_a')
        assert len(results) == 2

    def test_get_recon_queue(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_Q1', 100.0, '2026-01-01', 'oasys', 'e1')
        db.upsert_from_invoice('NVC_Q2', 200.0, 'Paid', 'tenant', 'PR001', 'USD')
        # Create a 2-way match
        db.upsert_from_remittance('NVC_Q3', 300.0, '2026-01-01', 'oasys', 'e2')
        db.upsert_from_invoice('NVC_Q3', 300.0, 'Paid', 'tenant', 'PR002', 'USD')

        records, total = db.get_recon_queue()
        assert total == 3
        # Priority: remittance_only and invoice_only should come before 2way_matched
        statuses = [r['match_status'] for r in records]
        # remittance_only=2, invoice_only=3 in priority ordering
        assert statuses.index('remittance_only') < statuses.index('2way_matched')

    def test_find_amount_suggestions(self, temp_db):
        db, _ = temp_db
        # Record with remittance but no invoice
        db.upsert_from_remittance('NVC_ALPHA_001', 1000.0, '2026-01-01', 'oasys', 'e1')
        # Another record with invoice matching the amount (within 1%)
        db.upsert_from_invoice('NVC_BETA_002', 1005.0, 'Paid', 'tenant', 'PR001', 'USD')
        # Another with very different amount and distinct NVC prefix
        db.upsert_from_invoice('NVC_GAMMA_003', 9999.0, 'Paid', 'tenant', 'PR002', 'USD')

        suggestions = db.find_amount_suggestions('NVC_ALPHA_001')
        assert len(suggestions) >= 1
        # The amount-based match should find NVC_BETA_002
        suggested_codes = [s['nvc_code'] for s in suggestions]
        assert 'NVC_BETA_002' in suggested_codes
        # NVC_GAMMA_003 should not appear (amount too far, prefix too different)
        assert 'NVC_GAMMA_003' not in suggested_codes


class TestReconSummary:
    """Test summary aggregation."""

    def test_get_recon_summary(self, temp_db):
        db, _ = temp_db
        db.upsert_from_remittance('NVC_R1', 100.0, '2026-01-01', 'oasys', 'e1')
        db.upsert_from_invoice('NVC_I1', 200.0, 'Paid', 'tenant', 'PR001', 'USD')
        db.upsert_from_remittance('NVC_M1', 300.0, '2026-01-01', 'oasys', 'e2')
        db.upsert_from_invoice('NVC_M1', 300.0, 'Paid', 'tenant', 'PR002', 'USD')

        summary = db.get_recon_summary()
        assert summary['total'] == 3
        assert summary.get('remittance_only', 0) == 1
        assert summary.get('invoice_only', 0) == 1
        assert summary.get('2way_matched', 0) == 1
