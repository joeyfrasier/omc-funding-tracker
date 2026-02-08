"""SQLite database for storing processed email records."""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path('data/processed_emails.db')


def _get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            subject TEXT,
            sender TEXT,
            email_date TEXT,
            fetched_at TEXT NOT NULL,
            attachment_count INTEGER DEFAULT 0,
            manual_review INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS remittances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT NOT NULL,
            agency TEXT,
            account_number TEXT,
            payment_date TEXT,
            payment_amount REAL,
            source_type TEXT,
            line_count INTEGER DEFAULT 0,
            matched_count INTEGER DEFAULT 0,
            mismatched_count INTEGER DEFAULT 0,
            not_found_count INTEGER DEFAULT 0,
            processed_at TEXT NOT NULL,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        );
        CREATE TABLE IF NOT EXISTS match_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            remittance_id INTEGER NOT NULL,
            nvc_code TEXT,
            description TEXT,
            company TEXT,
            remittance_amount REAL,
            db_amount REAL,
            difference REAL,
            status TEXT,
            tenant TEXT,
            db_status TEXT,
            payrun_ref TEXT,
            notes TEXT,
            FOREIGN KEY (remittance_id) REFERENCES remittances(id)
        );
    """)
    conn.commit()
    conn.close()
    logger.info("Email database initialized at %s", DB_PATH)


def store_email(email_data: dict):
    """Store a fetched email record."""
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO emails (id, source, subject, sender, email_date, fetched_at, attachment_count, manual_review)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email_data['id'],
            email_data.get('source', ''),
            email_data.get('subject', ''),
            email_data.get('from', ''),
            email_data.get('date', ''),
            datetime.now().isoformat(),
            len(email_data.get('attachments', [])),
            1 if email_data.get('manual_review') else 0,
        )
    )
    conn.commit()
    conn.close()


def store_reconciliation(email_id: str, report):
    """Store a reconciliation report linked to an email."""
    conn = _get_conn()
    cursor = conn.execute(
        """INSERT INTO remittances (email_id, agency, account_number, payment_date, payment_amount, source_type, line_count, matched_count, mismatched_count, not_found_count, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email_id,
            report.remittance.agency,
            report.remittance.account_number,
            report.remittance.payment_date,
            float(report.remittance.payment_amount),
            report.remittance.source_type,
            len(report.matches),
            report.matched_count,
            report.mismatched_count,
            report.not_found_count,
            datetime.now().isoformat(),
        )
    )
    rem_id = cursor.lastrowid
    
    for m in report.matches:
        conn.execute(
            """INSERT INTO match_results (remittance_id, nvc_code, description, company, remittance_amount, db_amount, difference, status, tenant, db_status, payrun_ref, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rem_id,
                m.nvc_code,
                m.remittance_line.description,
                m.remittance_line.company,
                float(m.remittance_amount),
                m.db_amount,
                m.difference,
                m.status,
                m.db_record.get('tenant', '').replace('.worksuite.com', '') if m.db_record else '',
                m.db_record.get('status', '') if m.db_record else '',
                m.db_record.get('payrun_reference', '') if m.db_record else '',
                m.notes,
            )
        )
    
    conn.commit()
    conn.close()
    logger.info("Stored reconciliation for email %s: %d matches", email_id, len(report.matches))
    return rem_id


def get_all_emails(limit=200, offset=0):
    """Get all stored emails with their remittance summaries."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT e.*, 
                  COUNT(r.id) as remittance_count,
                  SUM(r.matched_count) as total_matched,
                  SUM(r.mismatched_count) as total_mismatched,
                  SUM(r.not_found_count) as total_not_found,
                  SUM(r.payment_amount) as total_amount
           FROM emails e
           LEFT JOIN remittances r ON e.id = r.email_id
           GROUP BY e.id
           ORDER BY e.fetched_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_email_detail(email_id: str):
    """Get full detail for a single email including all match results."""
    conn = _get_conn()
    email = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    if not email:
        conn.close()
        return None
    
    result = dict(email)
    remittances = conn.execute(
        "SELECT * FROM remittances WHERE email_id = ? ORDER BY processed_at DESC", (email_id,)
    ).fetchall()
    
    result['remittances'] = []
    for rem in remittances:
        rem_dict = dict(rem)
        matches = conn.execute(
            "SELECT * FROM match_results WHERE remittance_id = ? ORDER BY id", (rem['id'],)
        ).fetchall()
        rem_dict['matches'] = [dict(m) for m in matches]
        result['remittances'].append(rem_dict)
    
    conn.close()
    return result


def get_stats():
    """Get aggregate stats."""
    conn = _get_conn()
    stats = {}
    stats['total_emails'] = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    stats['total_remittances'] = conn.execute("SELECT COUNT(*) FROM remittances").fetchone()[0]
    stats['total_matches'] = conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
    stats['matched'] = conn.execute("SELECT COUNT(*) FROM match_results WHERE status='matched'").fetchone()[0]
    stats['mismatched'] = conn.execute("SELECT COUNT(*) FROM match_results WHERE status='amount_mismatch'").fetchone()[0]
    stats['not_found'] = conn.execute("SELECT COUNT(*) FROM match_results WHERE status='not_in_db'").fetchone()[0]
    row = conn.execute("SELECT SUM(payment_amount) FROM remittances").fetchone()
    stats['total_value'] = row[0] or 0
    stats['sources'] = {}
    for row in conn.execute("SELECT source, COUNT(*) as cnt FROM emails GROUP BY source"):
        stats['sources'][row[0]] = row[1]
    conn.close()
    return stats


# Initialize on import
init_db()
