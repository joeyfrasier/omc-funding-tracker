"""SQLite database for reconciliation records — tracks 3-way matching."""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

RECON_DB_PATH = Path('data/recon.db')


def _get_conn():
    RECON_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_recon_db():
    """Create reconciliation tables if they don't exist."""
    conn = _get_conn()
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
            funding_amount REAL,
            funding_account_id TEXT,
            funding_date TEXT,
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
    """)
    conn.commit()
    conn.close()
    logger.info("Reconciliation database initialized at %s", RECON_DB_PATH)


def _now():
    return datetime.now().isoformat()


def upsert_from_remittance(nvc_code: str, amount: float, date: str, source: str, email_id: str):
    """Insert or update remittance leg for an NVC code."""
    conn = _get_conn()
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
    conn.close()
    recalculate_match_status(nvc_code)


def upsert_from_invoice(nvc_code: str, amount: float, status: str, tenant: str, payrun_ref: str, currency: str):
    """Insert or update invoice leg for an NVC code."""
    conn = _get_conn()
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
    conn.close()
    recalculate_match_status(nvc_code)


def upsert_from_funding(nvc_code: str, amount: float, account_id: str, date: str,
                        currency: str = '', status: str = '', recipient: str = '', recipient_country: str = ''):
    """Insert or update funding leg for an NVC code."""
    conn = _get_conn()
    now = _now()
    conn.execute("""
        INSERT INTO reconciliation_records (nvc_code, funding_amount, funding_account_id, funding_date,
            funding_currency, funding_status, funding_recipient, funding_recipient_country, first_seen_at, last_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(nvc_code) DO UPDATE SET
            funding_amount = excluded.funding_amount,
            funding_account_id = excluded.funding_account_id,
            funding_date = excluded.funding_date,
            funding_currency = excluded.funding_currency,
            funding_status = excluded.funding_status,
            funding_recipient = excluded.funding_recipient,
            funding_recipient_country = excluded.funding_recipient_country,
            last_updated_at = ?
    """, (nvc_code, amount, account_id, date, currency, status, recipient, recipient_country, now, now, now))
    conn.commit()
    conn.close()
    recalculate_match_status(nvc_code)


def recalculate_match_status(nvc_code: str):
    """Recalculate match_status and match_flags for a given NVC code."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM reconciliation_records WHERE nvc_code = ?", (nvc_code,)).fetchone()
    if not row:
        conn.close()
        return

    row = dict(row)
    has_remittance = row['remittance_amount'] is not None
    has_invoice = row['invoice_amount'] is not None
    has_funding = row['funding_amount'] is not None
    flags = []

    # If resolved, keep resolved status
    if row['resolved_at']:
        status = 'resolved'
    elif has_remittance and has_invoice and has_funding:
        # Check all 3 match
        if _amounts_match(row['remittance_amount'], row['invoice_amount']) and \
           _amounts_match(row['remittance_amount'], row['funding_amount']):
            status = 'full_3way'
        else:
            status = 'mismatch'
            if not _amounts_match(row['remittance_amount'], row['invoice_amount']):
                flags.append('remittance_invoice_mismatch')
            if not _amounts_match(row['remittance_amount'], row['funding_amount']):
                flags.append('remittance_funding_mismatch')
    elif has_remittance and has_invoice:
        if _amounts_match(row['remittance_amount'], row['invoice_amount']):
            status = 'partial_2way'
        else:
            status = 'mismatch'
            flags.append('amount_mismatch')
    elif has_remittance and has_funding:
        if _amounts_match(row['remittance_amount'], row['funding_amount']):
            status = 'partial_2way'
        else:
            status = 'mismatch'
            flags.append('amount_mismatch')
    elif has_invoice and has_funding:
        if _amounts_match(row['invoice_amount'], row['funding_amount']):
            status = 'partial_2way'
        else:
            status = 'mismatch'
            flags.append('invoice_funding_mismatch')
        flags.append('missing_remittance')
    elif has_remittance:
        status = 'remittance_only'
        flags.append('missing_invoice')
        flags.append('missing_funding')
    elif has_invoice:
        status = 'invoice_only'
        flags.append('missing_remittance')
        flags.append('missing_funding')
        flags.append('missing_funding')
    else:
        status = 'unmatched'

    conn.execute(
        "UPDATE reconciliation_records SET match_status = ?, match_flags = ?, last_updated_at = ? WHERE nvc_code = ?",
        (status, json.dumps(flags), _now(), nvc_code)
    )
    conn.commit()
    conn.close()


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
    conn = _get_conn()
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
    conn.close()
    return [dict(r) for r in rows]


def get_recon_record(nvc_code: str) -> Optional[Dict[str, Any]]:
    """Get a single reconciliation record."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM reconciliation_records WHERE nvc_code = ?", (nvc_code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_recon_summary() -> Dict[str, int]:
    """Get counts by match_status."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT match_status, COUNT(*) as cnt FROM reconciliation_records GROUP BY match_status"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM reconciliation_records").fetchone()[0]
    conn.close()
    summary = {r['match_status']: r['cnt'] for r in rows}
    summary['total'] = total
    return summary


def get_sync_state() -> List[Dict[str, Any]]:
    """Get sync state for all sources."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM sync_state ORDER BY source").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_sync_state(source: str, count: int, status: str = 'ok'):
    """Update sync state for a source."""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO sync_state (source, last_sync_at, last_count, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_sync_at = excluded.last_sync_at,
            last_count = excluded.last_count,
            status = excluded.status
    """, (source, _now(), count, status))
    conn.commit()
    conn.close()


def cache_payruns(payruns: List[Dict[str, Any]]):
    """Cache pay runs locally."""
    conn = _get_conn()
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
    conn.close()


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
    conn = _get_conn()
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
    conn.close()
    return [dict(r) for r in rows]


def _migrate_add_flag_columns():
    """Add flag/flag_notes columns if they don't exist (for existing DBs)."""
    conn = _get_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(reconciliation_records)").fetchall()]
        if 'flag' not in cols:
            conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag TEXT")
        if 'flag_notes' not in cols:
            conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag_notes TEXT")
        conn.commit()
    except Exception as e:
        logger.warning("Migration flag columns: %s", e)
    finally:
        conn.close()


def get_recon_records_queue(
    status: Optional[str] = None,
    tenant: Optional[str] = None,
    flag: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: str = 'priority',
    sort_dir: str = 'asc',
    limit: int = 50,
    offset: int = 0,
) -> tuple:
    """Get unreconciled records sorted by priority. Returns (records, total_count)."""
    conn = _get_conn()
    conditions = ["match_status NOT IN ('full_3way', 'resolved')"]
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
    if date_from:
        conditions.append("first_seen_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("first_seen_at <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}"

    # Count
    total = conn.execute(f"SELECT COUNT(*) FROM reconciliation_records {where}", params).fetchone()[0]

    # Priority ordering
    if sort_by == 'priority':
        order = """CASE match_status
            WHEN 'mismatch' THEN 1
            WHEN 'partial_2way' THEN 2
            WHEN 'remittance_only' THEN 3
            WHEN 'invoice_only' THEN 4
            WHEN 'unmatched' THEN 5
            ELSE 6
        END ASC, last_updated_at DESC"""
    else:
        allowed = {'last_updated_at', 'first_seen_at', 'remittance_amount', 'invoice_amount', 'funding_amount'}
        col = sort_by if sort_by in allowed else 'last_updated_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        order = f"{col} {direction}"

    rows = conn.execute(
        f"SELECT * FROM reconciliation_records {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


# Initialize on import
init_recon_db()
_migrate_add_flag_columns()
