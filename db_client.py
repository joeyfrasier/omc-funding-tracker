"""Database client for Worksuite aggregate DB (via SSH tunnel)."""
import logging
import os
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import List, Dict, Optional
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

def _require_env(name: str) -> str:
    """Get a required environment variable or raise."""
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return val

DB_HOST = _require_env('DB_HOST')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = _require_env('DB_NAME')
DB_USER = _require_env('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
SSH_BASTION = os.getenv('SSH_BASTION_HOST', '')
SSH_KEY = os.getenv('SSH_KEY_PATH', '')

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


SSH_TUNNEL_DISABLED = os.getenv('SSH_TUNNEL_DISABLED', '').lower() in ('true', '1', 'yes')

DB_CONNECT_TIMEOUT = int(os.getenv('DB_CONNECT_TIMEOUT', '10'))
DB_MAX_RETRIES = int(os.getenv('DB_MAX_RETRIES', '3'))


def _decimals_to_float(row: dict) -> dict:
    """Convert all Decimal values in a row dict to float for JSON serialization."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


@contextmanager
def get_connection():
    """Get a DB connection — direct or via SSH tunnel."""
    if SSH_TUNNEL_DISABLED:
        with _connect_direct() as conn:
            yield conn
    else:
        with _connect_via_tunnel() as conn:
            yield conn


@contextmanager
def _connect_direct():
    """Direct DB connection with retry."""
    last_error = None
    for attempt in range(1, DB_MAX_RETRIES + 1):
        try:
            logger.info("Connecting directly to %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                connect_timeout=DB_CONNECT_TIMEOUT,
            )
            break
        except Exception as e:
            last_error = e
            if attempt < DB_MAX_RETRIES:
                wait = 2 ** (attempt - 1)
                logger.warning("DB connection attempt %d/%d failed: %s — retrying in %ds",
                               attempt, DB_MAX_RETRIES, e, wait)
                time.sleep(wait)
            else:
                logger.error("DB connection failed after %d attempts: %s", DB_MAX_RETRIES, e)
                raise
    logger.info("Database connection established to %s/%s", DB_HOST, DB_NAME)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _connect_via_tunnel():
    """DB connection via SSH tunnel with retry."""
    import socket
    from sshtunnel import SSHTunnelForwarder
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(DB_CONNECT_TIMEOUT)
    logger.info("Opening SSH tunnel to %s via bastion %s", DB_HOST, SSH_BASTION)
    last_error = None
    for attempt in range(1, DB_MAX_RETRIES + 1):
        try:
            tunnel = SSHTunnelForwarder(
                (SSH_BASTION, int(os.getenv('SSH_BASTION_PORT', 22))),
                ssh_username='ec2-user',
                ssh_pkey=SSH_KEY,
                remote_bind_address=(DB_HOST, DB_PORT),
                set_keepalive=10,
            )
            tunnel.start()
            break
        except Exception as e:
            last_error = e
            if attempt < DB_MAX_RETRIES:
                wait = 2 ** (attempt - 1)
                logger.warning("SSH tunnel attempt %d/%d failed: %s — retrying in %ds",
                               attempt, DB_MAX_RETRIES, e, wait)
                time.sleep(wait)
            else:
                socket.setdefaulttimeout(old_timeout)
                logger.error("SSH tunnel failed after %d attempts: %s", DB_MAX_RETRIES, e)
                raise
    socket.setdefaulttimeout(old_timeout)
    logger.info("SSH tunnel established on local port %d", tunnel.local_bind_port)
    try:
        conn = psycopg2.connect(
            host='127.0.0.1',
            port=tunnel.local_bind_port,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=DB_CONNECT_TIMEOUT,
        )
        try:
            logger.info("Database connection established to %s/%s", DB_HOST, DB_NAME)
            yield conn
        finally:
            conn.close()
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
            row = _decimals_to_float(dict(row))
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
                p.id as payment_id,
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
        
        return [_decimals_to_float(dict(row)) for row in cur.fetchall()]


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
        
        return [_decimals_to_float(dict(row)) for row in cur.fetchall()]


def get_moneycorp_subaccounts() -> List[dict]:
    """Get MoneyCorp sub-accounts (processor_id) per OMC tenant with latest balances."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ON (tenant, currency)
                tenant, processor_id, currency, amount as balance,
                scheduled_amount, processing_amount,
                is_scheduled_currency_match, is_processing_currency_match,
                oper_fetch_date as last_updated
            FROM payments_operaccountbalance
            WHERE tenant = ANY(%s)
            ORDER BY tenant, currency, oper_fetch_date DESC
        """, (OMC_TENANTS,))
        
        return [_decimals_to_float(dict(row)) for row in cur.fetchall()]


def get_tenant_funding_config() -> List[dict]:
    """Get funding config (method) per OMC tenant."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT tenant, funding_method, created_at, updated_at
            FROM payments_paymentsconfig
            WHERE tenant = ANY(%s)
            ORDER BY tenant
        """, (OMC_TENANTS,))
        
        return [_decimals_to_float(dict(row)) for row in cur.fetchall()]


# Payment status codes (canonical — matches shortlist-platform pipeline/payments/common/model.py)
PAYMENT_STATUS = {
    0: 'New',
    1: 'Approved',
    2: 'Paid',
    3: 'Rejected',
    4: 'Scheduled',
    5: 'Processing',
    6: 'In Flight',
}

# Status tier classification for match-rate filtering
MATCHABLE_STATUSES = {'Approved', 'Scheduled', 'Processing', 'In Flight', 'Paid'}
PREMATCH_STATUSES = {'New'}
TERMINAL_STATUSES = {'Rejected'}


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
