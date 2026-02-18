"""SQLite database for reconciliation records — tracks 4-way matching.

4-Way Match Legs:
  1. Remittance — Gmail emails (OASYS/D365 ACH/LDN GSS)
  2. Invoice — Worksuite Aggregate DB
  3. Funding (incoming) — MoneyCorp receivedPayments (USD from customer)
  4. Payment (outgoing) — MoneyCorp payments (to contractor)
"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

RECON_DB_PATH = Path('data/recon.db')

# Agency name aliases for received payment matching
AGENCY_ALIASES = {
    "THE SCIENOMICS": ["Scienomics"],
    "ADELPHI RESEARCH": ["Adelphi Research Global"],
    "DDB CHICAGO INC.": ["DDB Chicago", "DDB"],
    "BBDO USA LLC": ["BBDO"],
    "ENERGY BBDO": ["Energy BBDO"],
    "FLEISHMANHILLARD": ["FleishmanHillard"],
    "TBWA WORLDWIDE": ["TBWA"],
    "OMNICOM MEDIA": ["Omnicom Media Group", "OMG"],
    "OMNICOM HEALTH": ["Omnicom Health Group", "OHG"],
}


@contextmanager
def _get_conn():
    RECON_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_recon_db():
    """Create reconciliation tables if they don't exist."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reconciliation_records (
                nvc_code TEXT PRIMARY KEY,
                remittance_amount REAL,
                remittance_date TEXT,
                remittance_source TEXT,
                remittance_email_id TEXT,
                invoice_amount REAL,
                invoice_status TEXT,
                invoice_tenant TEXT,
                invoice_payrun_ref TEXT,
                invoice_currency TEXT,
                payment_amount REAL,
                payment_account_id TEXT,
                payment_date TEXT,
                payment_currency TEXT,
                payment_status TEXT,
                payment_recipient TEXT,
                payment_recipient_country TEXT,
                received_payment_id TEXT,
                received_payment_amount REAL,
                received_payment_date TEXT,
                match_status TEXT DEFAULT 'unmatched',
                match_flags TEXT DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                notes TEXT,
                flag TEXT,
                flag_notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_recon_status ON reconciliation_records(match_status);
            CREATE INDEX IF NOT EXISTS idx_recon_tenant ON reconciliation_records(invoice_tenant);

            CREATE TABLE IF NOT EXISTS sync_state (
                source TEXT PRIMARY KEY,
                last_sync_at TEXT,
                last_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS cached_payruns (
                id INTEGER PRIMARY KEY,
                reference TEXT,
                tenant TEXT,
                status INTEGER,
                payment_count INTEGER,
                total_amount REAL,
                created_at TEXT,
                fetched_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_payruns_tenant ON cached_payruns(tenant);

            CREATE TABLE IF NOT EXISTS cached_invoices (
                nvc_code TEXT PRIMARY KEY,
                payment_id INTEGER,
                invoice_number TEXT,
                total_amount REAL,
                currency TEXT,
                status INTEGER,
                status_label TEXT,
                paid_date TEXT,
                processing_date TEXT,
                in_flight_date TEXT,
                tenant TEXT,
                payrun_id TEXT,
                created_at TEXT,
                fetched_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_invoices_tenant ON cached_invoices(tenant);
            CREATE INDEX IF NOT EXISTS idx_invoices_status ON cached_invoices(status_label);

            CREATE TABLE IF NOT EXISTS received_payments (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                account_name TEXT,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                payment_date TEXT,
                payment_status TEXT,
                payer_name TEXT,
                raw_info TEXT,
                msl_reference TEXT,
                created_on TEXT,
                matched_remittance_email_id TEXT,
                match_confidence REAL,
                match_method TEXT,
                match_status TEXT DEFAULT 'unmatched',
                matched_at TEXT,
                matched_by TEXT,
                notes TEXT,
                fetched_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rp_account ON received_payments(account_id);
            CREATE INDEX IF NOT EXISTS idx_rp_status ON received_payments(match_status);
            CREATE INDEX IF NOT EXISTS idx_rp_date ON received_payments(payment_date);
            CREATE INDEX IF NOT EXISTS idx_rp_payer ON received_payments(payer_name);
        """)
        conn.commit()
    logger.info("Reconciliation database initialized at %s", RECON_DB_PATH)


def _now():
    return datetime.now().isoformat()


def upsert_from_remittance(nvc_code: str, amount: float, date: str, source: str, email_id: str):
    """Insert or update remittance leg for an NVC code."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            INSERT INTO reconciliation_records (nvc_code, remittance_amount, remittance_date, remittance_source, remittance_email_id, first_seen_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvc_code) DO UPDATE SET
                remittance_amount = excluded.remittance_amount,
                remittance_date = excluded.remittance_date,
                remittance_source = excluded.remittance_source,
                remittance_email_id = excluded.remittance_email_id,
                last_updated_at = ?
        """, (nvc_code, amount, date, source, email_id, now, now, now))
        conn.commit()
    recalculate_match_status(nvc_code)


def upsert_from_invoice(nvc_code: str, amount: float, status: str, tenant: str, payrun_ref: str, currency: str):
    """Insert or update invoice leg for an NVC code."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            INSERT INTO reconciliation_records (nvc_code, invoice_amount, invoice_status, invoice_tenant, invoice_payrun_ref, invoice_currency, first_seen_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvc_code) DO UPDATE SET
                invoice_amount = excluded.invoice_amount,
                invoice_status = excluded.invoice_status,
                invoice_tenant = excluded.invoice_tenant,
                invoice_payrun_ref = excluded.invoice_payrun_ref,
                invoice_currency = excluded.invoice_currency,
                last_updated_at = ?
        """, (nvc_code, amount, status, tenant, payrun_ref, currency, now, now, now))
        conn.commit()
    recalculate_match_status(nvc_code)


def upsert_from_funding(nvc_code: str, amount: float, account_id: str, date: str,
                        currency: str = '', status: str = '', recipient: str = '', recipient_country: str = ''):
    """Insert or update funding/payment (outbound) leg for an NVC code.
    
    Note: This is the OUTBOUND payment leg (Leg 4). Column names are now payment_*.
    Function name kept for backward compatibility with sync_service.
    """
    upsert_from_payment(nvc_code, amount, account_id, date, currency, status, recipient, recipient_country)


def upsert_from_payment(nvc_code: str, amount: float, account_id: str, date: str,
                         currency: str = '', status: str = '', recipient: str = '', recipient_country: str = ''):
    """Insert or update payment (outbound) leg for an NVC code — Leg 4."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            INSERT INTO reconciliation_records (nvc_code, payment_amount, payment_account_id, payment_date,
                payment_currency, payment_status, payment_recipient, payment_recipient_country, first_seen_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvc_code) DO UPDATE SET
                payment_amount = excluded.payment_amount,
                payment_account_id = excluded.payment_account_id,
                payment_date = excluded.payment_date,
                payment_currency = excluded.payment_currency,
                payment_status = excluded.payment_status,
                payment_recipient = excluded.payment_recipient,
                payment_recipient_country = excluded.payment_recipient_country,
                last_updated_at = ?
        """, (nvc_code, amount, account_id, date, currency, status, recipient, recipient_country, now, now, now))
        conn.commit()
    recalculate_match_status(nvc_code)


def upsert_received_payment(payment_id: str, account_id: str, account_name: str,
                             amount: float, currency: str, payment_date: str,
                             payment_status: str, payer_name: str, raw_info: str,
                             msl_reference: str, created_on: str):
    """Insert or update a received payment record (incoming funding — Leg 3)."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            INSERT INTO received_payments (id, account_id, account_name, amount, currency, payment_date,
                payment_status, payer_name, raw_info, msl_reference, created_on, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_name = excluded.account_name,
                amount = excluded.amount,
                currency = excluded.currency,
                payment_date = excluded.payment_date,
                payment_status = excluded.payment_status,
                payer_name = excluded.payer_name,
                raw_info = excluded.raw_info,
                msl_reference = excluded.msl_reference,
                created_on = excluded.created_on,
                fetched_at = excluded.fetched_at
        """, (payment_id, account_id, account_name, amount, currency, payment_date,
              payment_status, payer_name, raw_info, msl_reference, created_on, now))
        conn.commit()


def link_received_payment_to_nvc(nvc_code: str, received_payment_id: str,
                                  amount: float, date: str):
    """Link a received payment to an NVC code in reconciliation_records (Leg 3)."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            UPDATE reconciliation_records SET
                received_payment_id = ?,
                received_payment_amount = ?,
                received_payment_date = ?,
                last_updated_at = ?
            WHERE nvc_code = ?
        """, (received_payment_id, amount, date, now, nvc_code))
        conn.commit()
    recalculate_match_status(nvc_code)


def get_received_payments(
    account_id: Optional[str] = None,
    match_status: Optional[str] = None,
    payer: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple:
    """Get received payments with filters. Returns (records, total)."""
    with _get_conn() as conn:
        conditions = []
        params: list = []

        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        if match_status:
            conditions.append("match_status = ?")
            params.append(match_status)
        if payer:
            conditions.append("payer_name LIKE ?")
            params.append(f"%{payer}%")
        if date_from:
            conditions.append("payment_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("payment_date <= ?")
            params.append(date_to)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = conn.execute(f"SELECT COUNT(*) FROM received_payments {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM received_payments {where} ORDER BY payment_date DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows], total


def get_received_payment(payment_id: str) -> Optional[Dict[str, Any]]:
    """Get a single received payment."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM received_payments WHERE id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def get_received_payments_summary() -> Dict[str, Any]:
    """Get received payments summary."""
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM received_payments").fetchone()[0]
        total_amount = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM received_payments").fetchone()[0]
        by_status = conn.execute(
            "SELECT match_status, COUNT(*) as cnt, SUM(amount) as total FROM received_payments GROUP BY match_status"
        ).fetchall()
    return {
        'total': total,
        'total_amount': total_amount,
        'by_status': {r['match_status']: {'count': r['cnt'], 'amount': r['total']} for r in by_status},
    }


def match_received_payment(payment_id: str, email_id: str, confidence: float,
                            method: str, matched_by: str = 'auto'):
    """Match a received payment to a remittance email."""
    with _get_conn() as conn:
        now = _now()
        conn.execute("""
            UPDATE received_payments SET
                matched_remittance_email_id = ?,
                match_confidence = ?,
                match_method = ?,
                match_status = 'matched',
                matched_at = ?,
                matched_by = ?
            WHERE id = ?
        """, (email_id, confidence, method, now, matched_by, payment_id))
        conn.commit()


def unmatch_received_payment(payment_id: str):
    """Undo a received payment match."""
    with _get_conn() as conn:
        now = _now()
        # Get the payment to find linked NVCs
        rp = conn.execute("SELECT matched_remittance_email_id FROM received_payments WHERE id = ?", (payment_id,)).fetchone()

        conn.execute("""
            UPDATE received_payments SET
                matched_remittance_email_id = NULL,
                match_confidence = NULL,
                match_method = NULL,
                match_status = 'unmatched',
                matched_at = NULL,
                matched_by = NULL
            WHERE id = ?
        """, (payment_id,))

        # Clear received_payment linkage from reconciliation_records
        conn.execute("""
            UPDATE reconciliation_records SET
                received_payment_id = NULL,
                received_payment_amount = NULL,
                received_payment_date = NULL,
                last_updated_at = ?
            WHERE received_payment_id = ?
        """, (now, payment_id))

        conn.commit()


def recalculate_match_status(nvc_code: str):
    """Recalculate match_status and match_flags for 4-way matching.

    Legs: remittance, invoice, funding (received_payment), payment (outbound).
    """
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM reconciliation_records WHERE nvc_code = ?", (nvc_code,)).fetchone()
        if not row:
            return

        row = dict(row)
        has_remittance = row['remittance_amount'] is not None
        has_invoice = row['invoice_amount'] is not None
        has_funding = row.get('received_payment_id') is not None  # Leg 3: inbound USD
        has_payment = row.get('payment_amount') is not None       # Leg 4: outbound to contractor
        flags = []

        # If resolved, keep resolved status
        if row['resolved_at']:
            status = 'resolved'
        elif has_remittance and has_invoice and has_funding and has_payment:
            # All 4 legs present
            if _amounts_match(row['remittance_amount'], row['invoice_amount']):
                status = 'full_4way'
            else:
                status = 'amount_mismatch'
                if not _amounts_match(row['remittance_amount'], row['invoice_amount']):
                    flags.append('remittance_invoice_mismatch')
        elif has_remittance and has_invoice and has_funding:
            # 3 legs: funded but not yet paid out
            if _amounts_match(row['remittance_amount'], row['invoice_amount']):
                status = '3way_awaiting_payment'
            else:
                status = 'amount_mismatch'
                flags.append('remittance_invoice_mismatch')
        elif has_remittance and has_invoice and has_payment:
            # 3 legs: paid out but no inbound funding record
            if _amounts_match(row['remittance_amount'], row['invoice_amount']):
                status = '3way_no_funding'
            else:
                status = 'amount_mismatch'
                flags.append('remittance_invoice_mismatch')
        elif has_remittance and has_invoice:
            if _amounts_match(row['remittance_amount'], row['invoice_amount']):
                status = '2way_matched'
            else:
                status = 'amount_mismatch'
                flags.append('amount_mismatch')
        elif has_invoice and has_payment:
            status = 'invoice_payment_only'
            flags.append('missing_remittance')
        elif has_remittance:
            status = 'remittance_only'
            flags.append('missing_invoice')
        elif has_invoice:
            status = 'invoice_only'
            flags.append('missing_remittance')
        elif has_payment:
            status = 'payment_only'
            flags.append('missing_remittance')
            flags.append('missing_invoice')
        else:
            status = 'unmatched'

        conn.execute(
            "UPDATE reconciliation_records SET match_status = ?, match_flags = ?, last_updated_at = ? WHERE nvc_code = ?",
            (status, json.dumps(flags), _now(), nvc_code)
        )
        conn.commit()


def _amounts_match(a: float, b: float, tolerance: float = 0.01) -> bool:
    """Check if two amounts match within tolerance."""
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


def get_recon_records(
    status: Optional[str] = None,
    tenant: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get reconciliation records with optional filters."""
    with _get_conn() as conn:
        conditions = []
        params: list = []

        if status:
            conditions.append("match_status = ?")
            params.append(status)
        if tenant:
            conditions.append("invoice_tenant LIKE ?")
            params.append(f"%{tenant}%")
        if search:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{search}%")
        if date_from:
            conditions.append("first_seen_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("first_seen_at <= ?")
            params.append(date_to)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM reconciliation_records {where} ORDER BY last_updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows]


def get_recon_record(nvc_code: str) -> Optional[Dict[str, Any]]:
    """Get a single reconciliation record."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM reconciliation_records WHERE nvc_code = ?", (nvc_code,)).fetchone()
    return dict(row) if row else None


def get_recon_summary() -> Dict[str, int]:
    """Get counts by match_status."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT match_status, COUNT(*) as cnt FROM reconciliation_records GROUP BY match_status"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM reconciliation_records").fetchone()[0]
    summary = {r['match_status']: r['cnt'] for r in rows}
    summary['total'] = total
    return summary


def get_sync_state() -> List[Dict[str, Any]]:
    """Get sync state for all sources."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM sync_state ORDER BY source").fetchall()
    return [dict(r) for r in rows]


def update_sync_state(source: str, count: int, status: str = 'ok'):
    """Update sync state for a source."""
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (source, last_sync_at, last_count, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_sync_at = excluded.last_sync_at,
                last_count = excluded.last_count,
                status = excluded.status
        """, (source, _now(), count, status))
        conn.commit()


def cache_payruns(payruns: List[Dict[str, Any]]):
    """Cache pay runs locally."""
    with _get_conn() as conn:
        now = _now()
        for p in payruns:
            conn.execute("""
                INSERT OR REPLACE INTO cached_payruns (id, reference, tenant, status, payment_count, total_amount, created_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p.get('id'), p.get('reference'), p.get('tenant'), p.get('status'),
                p.get('payment_count'), p.get('total_amount'), p.get('created_at'), now
            ))
        conn.commit()


def get_cached_payruns(
    tenant: Optional[str] = None,
    status: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = 'created_at',
    sort_dir: str = 'desc',
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get cached pay runs with filters."""
    with _get_conn() as conn:
        conditions = []
        params: list = []

        if tenant:
            conditions.append("tenant LIKE ?")
            params.append(f"%{tenant}%")
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to)
        if search:
            conditions.append("(reference LIKE ? OR tenant LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        allowed_sorts = {'created_at', 'total_amount', 'tenant', 'reference', 'fetched_at'}
        sort_col = sort_by if sort_by in allowed_sorts else 'created_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'

        rows = conn.execute(
            f"SELECT * FROM cached_payruns {where} ORDER BY {sort_col} {direction} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows]


def cache_invoices(invoices: List[Dict[str, Any]]):
    """Cache invoices locally."""
    with _get_conn() as conn:
        now = _now()
        for inv in invoices:
            conn.execute("""
                INSERT OR REPLACE INTO cached_invoices
                (nvc_code, payment_id, invoice_number, total_amount, currency, status, status_label,
                 paid_date, processing_date, in_flight_date, tenant, payrun_id, created_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inv.get('nvc_code'), inv.get('payment_id'), inv.get('invoice_number'), inv.get('total_amount'),
                inv.get('currency', ''), inv.get('status'), inv.get('status_label', ''),
                inv.get('paid_date', ''), inv.get('processing_date', ''),
                inv.get('in_flight_date', ''), inv.get('tenant', ''),
                inv.get('payrun_id', ''), inv.get('created_at', ''), now,
            ))
        conn.commit()


def get_cached_invoices(
    tenant: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = 'created_at',
    sort_dir: str = 'desc',
    limit: int = 200,
    offset: int = 0,
) -> tuple:
    """Get cached invoices with filters. Returns (records, total_count)."""
    with _get_conn() as conn:
        conditions: list = []
        params: list = []

        if tenant:
            conditions.append("tenant LIKE ?")
            params.append(f"%{tenant}%")
        if status:
            conditions.append("status_label = ?")
            params.append(status)
        if search:
            conditions.append("(nvc_code LIKE ? OR invoice_number LIKE ? OR tenant LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = conn.execute(f"SELECT COUNT(*) FROM cached_invoices {where}", params).fetchone()[0]

        allowed_sorts = {'created_at', 'total_amount', 'tenant', 'nvc_code', 'status_label', 'fetched_at'}
        sort_col = sort_by if sort_by in allowed_sorts else 'created_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'

        rows = conn.execute(
            f"SELECT * FROM cached_invoices {where} ORDER BY {sort_col} {direction} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows], total


def _migrate_add_flag_columns():
    """Add flag/flag_notes columns if they don't exist (for existing DBs)."""
    with _get_conn() as conn:
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reconciliation_records)").fetchall()]
            if 'flag' not in cols:
                conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag TEXT")
            if 'flag_notes' not in cols:
                conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag_notes TEXT")
            conn.commit()
        except Exception as e:
            logger.warning("Migration flag columns: %s", e)


def _migrate_4way_columns():
    """Rename funding_* → payment_* and add received_payment_* columns for 4-way matching."""
    with _get_conn() as conn:
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reconciliation_records)").fetchall()]

            # Rename funding_* → payment_* (outbound to contractor)
            renames = {
                'funding_amount': 'payment_amount',
                'funding_account_id': 'payment_account_id',
                'funding_date': 'payment_date',
                'funding_currency': 'payment_currency',
                'funding_status': 'payment_status',
                'funding_recipient': 'payment_recipient',
                'funding_recipient_country': 'payment_recipient_country',
            }
            for old_col, new_col in renames.items():
                if old_col in cols and new_col not in cols:
                    conn.execute(f"ALTER TABLE reconciliation_records RENAME COLUMN {old_col} TO {new_col}")
                    logger.info("Renamed column %s → %s", old_col, new_col)

            # Re-read columns after renames
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reconciliation_records)").fetchall()]

            # Ensure payment_* columns exist (may be missing if DB was created
            # before these were added to the schema)
            payment_cols = {
                'payment_currency': 'TEXT',
                'payment_status': 'TEXT',
                'payment_recipient': 'TEXT',
                'payment_recipient_country': 'TEXT',
            }
            for col, dtype in payment_cols.items():
                if col not in cols:
                    conn.execute(f"ALTER TABLE reconciliation_records ADD COLUMN {col} {dtype}")
                    logger.info("Added missing column %s", col)

            # Re-read columns after additions
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reconciliation_records)").fetchall()]

            # Add received payment columns (incoming funding)
            new_cols = {
                'received_payment_id': 'TEXT',
                'received_payment_amount': 'REAL',
                'received_payment_date': 'TEXT',
            }
            for col, dtype in new_cols.items():
                if col not in cols:
                    conn.execute(f"ALTER TABLE reconciliation_records ADD COLUMN {col} {dtype}")
                    logger.info("Added column %s", col)

            conn.commit()
        except Exception as e:
            logger.warning("Migration 4-way columns: %s", e)


def get_recon_queue(
    status: Optional[str] = None,
    tenant: Optional[str] = None,
    flag: Optional[str] = None,
    search: Optional[str] = None,
    invoice_status: Optional[str] = None,
    sort_by: str = 'last_updated_at',
    sort_dir: str = 'desc',
    limit: int = 100,
    offset: int = 0,
) -> tuple:
    """Get reconciliation queue with priority ordering. Returns (records, total)."""
    with _get_conn() as conn:
        conditions: list = []
        params: list = []

        if status:
            conditions.append("match_status = ?")
            params.append(status)
        if tenant:
            conditions.append("invoice_tenant LIKE ?")
            params.append(f"%{tenant}%")
        if flag:
            conditions.append("flag = ?")
            params.append(flag)
        if search:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{search}%")
        if invoice_status:
            conditions.append("invoice_status = ?")
            params.append(invoice_status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = conn.execute(f"SELECT COUNT(*) FROM reconciliation_records {where}", params).fetchone()[0]

        allowed_sorts = {'last_updated_at', 'first_seen_at', 'invoice_amount', 'remittance_amount', 'payment_amount'}
        sort_col = sort_by if sort_by in allowed_sorts else 'last_updated_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'

        order = f"""
            CASE match_status
                WHEN 'amount_mismatch' THEN 1
                WHEN 'remittance_only' THEN 2
                WHEN 'invoice_only' THEN 3
                WHEN 'payment_only' THEN 4
                WHEN 'invoice_payment_only' THEN 5
                WHEN '2way_matched' THEN 6
                WHEN '3way_no_funding' THEN 7
                WHEN '3way_awaiting_payment' THEN 8
                WHEN 'full_4way' THEN 9
                WHEN 'resolved' THEN 10
            END, {sort_col} {direction}
        """

        rows = conn.execute(
            f"SELECT * FROM reconciliation_records {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return [dict(r) for r in rows], total


def get_agency_stats() -> List[Dict[str, Any]]:
    """Get reconciliation stats grouped by tenant for the overview dashboard."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT invoice_tenant,
                   COUNT(*) as total_records,
                   SUM(CASE WHEN match_status IN ('full_4way', '3way_awaiting_payment', '3way_no_funding', '2way_matched') THEN 1 ELSE 0 END) as reconciled,
                   SUM(CASE WHEN match_status = 'full_4way' THEN 1 ELSE 0 END) as full_4way,
                   SUM(CASE WHEN match_status IN ('amount_mismatch', 'invoice_only', 'remittance_only', 'payment_only', 'unmatched') THEN 1 ELSE 0 END) as unreconciled,
                   SUM(COALESCE(invoice_amount, 0)) as total_value
            FROM reconciliation_records
            WHERE invoice_tenant IS NOT NULL AND invoice_tenant != ''
            GROUP BY invoice_tenant
            ORDER BY total_value DESC
        """).fetchall()
    return [{
        'name': r['invoice_tenant'],
        'count': r['total_records'],
        'total': r['total_value'],
        'reconciled_count': r['reconciled'],
        'full_4way_count': r['full_4way'],
        'unreconciled_count': r['unreconciled'],
    } for r in rows]


def get_nvc_codes_for_email(email_id: str) -> List[str]:
    """Get NVC codes associated with a remittance email."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT nvc_code FROM reconciliation_records WHERE remittance_email_id = ?",
            (email_id,)
        ).fetchall()
    return [r['nvc_code'] for r in rows]


def get_email_remittance_totals() -> List[Dict[str, Any]]:
    """Get aggregated remittance totals per email ID."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT remittance_email_id,
                   SUM(remittance_amount) as total_amount,
                   MIN(remittance_date) as date,
                   COUNT(*) as nvc_count
            FROM reconciliation_records
            WHERE remittance_email_id IS NOT NULL
            GROUP BY remittance_email_id
        """).fetchall()
    return [dict(r) for r in rows]


def update_recon_flag(nvc_code: str, flag: Optional[str], notes: Optional[str] = None):
    """Set flag and notes on a reconciliation record."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE reconciliation_records SET flag = ?, flag_notes = ?, last_updated_at = ? WHERE nvc_code = ?",
            (flag or None, notes or None, _now(), nvc_code)
        )
        conn.commit()


def append_recon_note(nvc_code: str, note: str):
    """Append an audit note to a reconciliation record."""
    with _get_conn() as conn:
        row = conn.execute("SELECT notes FROM reconciliation_records WHERE nvc_code = ?", (nvc_code,)).fetchone()
        existing = (row['notes'] or '') if row else ''
        new_notes = f"{existing}\n{note}".strip()
        conn.execute(
            "UPDATE reconciliation_records SET notes = ?, last_updated_at = ? WHERE nvc_code = ?",
            (new_notes, _now(), nvc_code)
        )
        conn.commit()


def search_recon_records(
    amount_field: str,
    nvc_search: Optional[str] = None,
    tenant: Optional[str] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search reconciliation records by amount field with filters."""
    with _get_conn() as conn:
        allowed_fields = {'remittance_amount', 'invoice_amount', 'payment_amount'}
        if amount_field not in allowed_fields:
            return []
        conditions = [f"{amount_field} IS NOT NULL"]
        params: list = []
        if nvc_search:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{nvc_search}%")
        if tenant:
            conditions.append("invoice_tenant LIKE ?")
            params.append(f"%{tenant}%")
        if amount_min is not None:
            conditions.append(f"{amount_field} >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append(f"{amount_field} <= ?")
            params.append(amount_max)
        where = f"WHERE {' AND '.join(conditions)}"
        rows = conn.execute(
            f"SELECT * FROM reconciliation_records {where} ORDER BY last_updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def find_amount_suggestions(nvc_code: str) -> List[Dict[str, Any]]:
    """Find potential amount-based matches for suggestions."""
    record = get_recon_record(nvc_code)
    if not record:
        return []

    with _get_conn() as conn:
        suggestions = []
        seen: set = set()

        # Amount-based matches (±1%)
        for amt_field, src_label in [
            ('remittance_amount', 'remittance'),
            ('invoice_amount', 'invoice'),
            ('payment_amount', 'payment'),
        ]:
            amt = record.get(amt_field)
            if amt is None:
                continue
            tolerance = amt * 0.01
            lo, hi = amt - tolerance, amt + tolerance
            for other_field, other_label in [
                ('remittance_amount', 'remittance'),
                ('invoice_amount', 'invoice'),
                ('payment_amount', 'payment'),
            ]:
                if other_label == src_label:
                    continue
                if record.get(other_field) is not None:
                    continue
                rows = conn.execute(f"""
                    SELECT * FROM reconciliation_records
                    WHERE nvc_code != ? AND {other_field} BETWEEN ? AND ?
                    LIMIT 10
                """, (nvc_code, lo, hi)).fetchall()
                for r in rows:
                    rk = r['nvc_code']
                    if rk in seen:
                        continue
                    seen.add(rk)
                    confidence = 0.7
                    if record.get('invoice_tenant') and r['invoice_tenant'] == record.get('invoice_tenant'):
                        confidence += 0.15
                    suggestions.append({
                        'nvc_code': rk,
                        'reason': f"Amount match ({other_label}: {r[other_field]:.2f})",
                        'confidence': round(confidence, 2),
                        'record': dict(r),
                    })

        # Fuzzy NVC code match (prefix)
        if len(nvc_code) > 4:
            prefix = nvc_code[:len(nvc_code) - 2]
            rows = conn.execute(
                "SELECT * FROM reconciliation_records WHERE nvc_code LIKE ? AND nvc_code != ? LIMIT 10",
                (f"{prefix}%", nvc_code)
            ).fetchall()
            for r in rows:
                rk = r['nvc_code']
                if rk in seen:
                    continue
                seen.add(rk)
                suggestions.append({
                    'nvc_code': rk,
                    'reason': f"Similar NVC code ({rk})",
                    'confidence': 0.5,
                    'record': dict(r),
                })

    suggestions.sort(key=lambda x: x['confidence'], reverse=True)
    return suggestions[:5]


# Initialize on import
init_recon_db()
_migrate_add_flag_columns()
_migrate_4way_columns()
