"""Database client for Worksuite aggregate DB (via SSH tunnel)."""
import logging
import os
from contextlib import contextmanager
from decimal import Decimal
from typing import List, Dict, Optional
from sshtunnel import SSHTunnelForwarder
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_HOST = os.getenv('DB_HOST', 'aggregate.ctq4nnj79yij.eu-west-1.rds.amazonaws.com')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'production')
DB_USER = os.getenv('DB_USER', 'customersuccess')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
SSH_BASTION = os.getenv('SSH_BASTION_HOST', '34.241.37.218')
SSH_KEY = os.getenv('SSH_KEY_PATH', '/Users/joey/.ssh/db-bastion.pem')

# Omnicom tenant patterns
OMC_TENANTS = [
    'omcbbdo.worksuite.com',
    'omcflywheel.worksuite.com',
    'omcohg.worksuite.com',
    'omnicombranding.worksuite.com',
    'omnicomddb.worksuite.com',
    'omnicommedia.worksuite.com',
    'omnicomoac.worksuite.com',
    'omnicomprecision.worksuite.com',
    'omnicomprg.worksuite.com',
    'omnicomtbwa.worksuite.com',
    'omnicom.worksuite.com',
]


@contextmanager
def get_connection():
    """Get a DB connection through SSH tunnel."""
    import socket
    socket.setdefaulttimeout(10)  # Global 10s timeout for SSH connection
    logger.info("Opening SSH tunnel to %s via bastion %s", DB_HOST, SSH_BASTION)
    try:
        tunnel = SSHTunnelForwarder(
            (SSH_BASTION, int(os.getenv('SSH_BASTION_PORT', 22))),
            ssh_username='ec2-user',
            ssh_pkey=SSH_KEY,
            remote_bind_address=(DB_HOST, DB_PORT),
            set_keepalive=10,
        )
        tunnel.start()
    finally:
        socket.setdefaulttimeout(None)  # Reset
    logger.info("SSH tunnel established on local port %d", tunnel.local_bind_port)
    try:
        conn = psycopg2.connect(
            host='127.0.0.1',
            port=tunnel.local_bind_port,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        logger.info("Database connection established to %s/%s", DB_HOST, DB_NAME)
        yield conn
        conn.close()
    except Exception as e:
        logger.error("Database connection failed: %s", e, exc_info=True)
        raise
    finally:
        tunnel.stop()
        logger.info("SSH tunnel closed")


def lookup_payments_by_nvc(nvc_codes: List[str]) -> Dict[str, dict]:
    """Look up payments by NVC codes (invoice_id field).
    
    Returns dict mapping NVC code -> payment record.
    """
    if not nvc_codes:
        logger.debug("lookup_payments_by_nvc called with empty list")
        return {}
    
    logger.info("Looking up %d NVC codes in database", len(nvc_codes))
    
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT 
                p.invoice_id as nvc_code,
                p.number as invoice_number,
                p.total_amount,
                p.total_amount_without_tax,
                p.total_tax,
                p.currency,
                p.funding_currency,
                p.status,
                p.payment_method,
                p.paid_date,
                p.paid_day,
                p.created_at,
                p.processing_date,
                p.in_flight_date,
                p.tenant,
                p.payrun_id,
                p.worker_id,
                p.vendor_id,
                pr.reference as payrun_reference,
                pr.status as payrun_status,
                pr.batch_reference
            FROM documents_payment p
            LEFT JOIN documents_payrun pr ON p.payrun_id = pr.id AND p.tenant = pr.tenant
            WHERE p.invoice_id = ANY(%s)
        """, (nvc_codes,))
        
        results = {}
        for row in cur.fetchall():
            row = dict(row)
            # Convert Decimal for JSON serialization
            for k, v in row.items():
                if isinstance(v, Decimal):
                    row[k] = float(v)
            results[row['nvc_code']] = row
        
        found = len(results)
        missing = len(nvc_codes) - found
        logger.info("NVC lookup complete: %d/%d found, %d missing", found, len(nvc_codes), missing)
        if missing > 0:
            missing_codes = [c for c in nvc_codes if c not in results]
            logger.warning("Missing NVC codes: %s", missing_codes[:10])
        return results


def get_omc_payments(days_back=60, status=None) -> List[dict]:
    """Get recent OMC payments."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = """
            SELECT 
                p.invoice_id as nvc_code,
                p.number as invoice_number,
                p.total_amount,
                p.currency,
                p.status,
                p.paid_date,
                p.processing_date,
                p.in_flight_date,
                p.tenant,
                p.payrun_id,
                p.created_at
            FROM documents_payment p
            WHERE p.tenant = ANY(%s)
            AND p.created_at > NOW() - INTERVAL '%s days'
        """
        params = [OMC_TENANTS, days_back]
        
        if status is not None:
            query += " AND p.status = %s"
            params.append(status)
        
        query += " ORDER BY p.created_at DESC"
        cur.execute(query, params)
        
        results = []
        for row in cur.fetchall():
            row = dict(row)
            for k, v in row.items():
                if isinstance(v, Decimal):
                    row[k] = float(v)
            results.append(row)
        return results


def get_omc_payruns(days_back=60) -> List[dict]:
    """Get recent OMC pay runs."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT 
                pr.id, pr.reference, pr.batch_reference, pr.status,
                pr.created_at, pr.processing_started_at, pr.tenant,
                COUNT(p.id) as payment_count,
                SUM(p.total_amount) as total_amount
            FROM documents_payrun pr
            LEFT JOIN documents_payment p ON p.payrun_id = pr.id AND p.tenant = pr.tenant
            WHERE pr.tenant = ANY(%s)
            AND pr.created_at > NOW() - INTERVAL '%s days'
            GROUP BY pr.id, pr.reference, pr.batch_reference, pr.status,
                     pr.created_at, pr.processing_started_at, pr.tenant
            ORDER BY pr.created_at DESC
        """, (OMC_TENANTS, days_back))
        
        results = []
        for row in cur.fetchall():
            row = dict(row)
            for k, v in row.items():
                if isinstance(v, Decimal):
                    row[k] = float(v)
            results.append(row)
        return results


# Payment status codes
PAYMENT_STATUS = {
    0: 'Draft',
    1: 'Approved',
    2: 'Processing', 
    3: 'In Flight',
    4: 'Paid',
    5: 'Rejected',
    6: 'Cancelled',
}


def status_label(code):
    return PAYMENT_STATUS.get(code, f'Unknown({code})')


if __name__ == '__main__':
    print("Testing DB connection...")
    payments = get_omc_payments(days_back=30)
    print(f"Found {len(payments)} OMC payments in last 30 days")
    for p in payments[:5]:
        print(f"  {p['nvc_code']} | {p['tenant']:30} | ${p['total_amount']:>10,.2f} | status={status_label(p['status'])}")
    
    print("\nPay runs:")
    payruns = get_omc_payruns(days_back=30)
    print(f"Found {len(payruns)} OMC pay runs in last 30 days")
    for pr in payruns[:5]:
        print(f"  {pr['reference']} | {pr['tenant']:30} | ${pr['total_amount'] or 0:>10,.2f} | {pr['status']} | {pr['payment_count']} payments")
