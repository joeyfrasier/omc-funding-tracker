"""FastAPI backend for OMC Funding Tracker.

Wraps existing Python modules (gmail_client, db_client, csv_parser, matcher, email_db)
as a REST API consumed by the Next.js frontend.
"""
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from db_client import (
    get_omc_payments, get_omc_payruns, status_label, OMC_TENANTS,
    get_moneycorp_subaccounts, get_tenant_funding_config,
)
from gmail_client import (
    fetch_all_remittances, fetch_emails, load_processed,
    mark_processed, EMAIL_SOURCES,
)
from csv_parser import parse_email_attachments
from matcher import reconcile_batch
from email_db import (
    store_email, store_reconciliation, get_all_emails,
    get_email_detail, get_stats, init_db,
)
import recon_db
from recon_db import (
    get_recon_records, get_recon_record, get_recon_summary,
    get_sync_state, get_cached_payruns,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# ── Background sync ──────────────────────────────────────────────────────

async def periodic_sync():
    while True:
        try:
            from sync_service import run_sync_cycle
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_sync_cycle)
        except Exception as e:
            logger.error("Periodic sync failed: %s", e)
        await asyncio.sleep(300)  # 5 minutes


from contextlib import asynccontextmanager
import asyncio


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(periodic_sync())
    yield
    task.cancel()


app = FastAPI(
    title="OMC Funding Tracker API",
    version="2.1.0",
    description="Omnicom Pay Run Funding — Remittance ↔ DB ↔ MoneyCorp reconciliation",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def serialize(obj):
    """Recursively convert Decimals/datetimes for JSON."""
    return json.loads(json.dumps(obj, cls=DecimalEncoder))


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "omc-funding-tracker", "version": "2.0.0"}


# ── Overview / Stats ─────────────────────────────────────────────────────

@app.get("/api/overview")
def overview(days: int = Query(7, ge=1, le=365)):
    """Dashboard overview: payments + reconciliation stats.
    
    Resilient: runs DB/Gmail checks in parallel with a 12s timeout.
    Returns partial data if any service is unreachable.
    """
    errors = {}
    services = {}
    payments = []
    processed = []
    recon_stats = {
        "total_emails": 0, "total_remittances": 0,
        "matched": 0, "mismatched": 0, "not_found": 0, "total_value": 0,
    }

    # Local stats always available (SQLite)
    try:
        recon_stats = get_stats()
    except Exception:
        pass

    # Pull match rates from new recon DB
    recon_summary_data = {}
    try:
        recon_summary_data = get_recon_summary()
    except Exception:
        pass

    # Run DB and Gmail checks in parallel with timeout
    def _fetch_db():
        return get_omc_payments(days_back=days)

    def _fetch_gmail():
        return load_processed()

    with ThreadPoolExecutor(max_workers=2) as executor:
        db_future = executor.submit(_fetch_db)
        gmail_future = executor.submit(_fetch_gmail)

        try:
            payments = db_future.result(timeout=12)
            services["db"] = "connected"
        except Exception as e:
            errors["db"] = str(e)[:100]
            services["db"] = "unreachable"

        try:
            processed = gmail_future.result(timeout=12)
            services["gmail"] = "connected"
        except Exception as e:
            errors["gmail"] = str(e)[:100]
            services["gmail"] = "unreachable"

    total_issues = recon_stats["mismatched"] + recon_stats["not_found"]
    total_lines = recon_stats["matched"] + total_issues

    # 3-way match rate: Remittance ↔ Worksuite ↔ MoneyCorp
    # Currently MoneyCorp verification is not yet integrated,
    # so all "matched" items are only 2-way (remittance ↔ Worksuite).
    # Items without MoneyCorp confirmation count as unverified.
    # Use new recon DB if populated, fall back to legacy stats
    recon_total = recon_summary_data.get('total', 0)
    if recon_total > 0:
        three_way_matched = recon_summary_data.get('full_3way', 0)
        two_way_matched = recon_summary_data.get('partial_2way', 0) + three_way_matched
        total_to_verify = recon_total
        mismatched_count = recon_summary_data.get('mismatch', 0)
    else:
        two_way_matched = recon_stats["matched"]
        three_way_matched = 0
        total_to_verify = recon_stats.get("total_matches", total_lines) or total_lines
        mismatched_count = recon_stats["mismatched"]

    match_rate_3way = (three_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    match_rate_2way = (two_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    unverified = total_to_verify - three_way_matched

    # Group breakdown with reconciliation status (all-time from recon DB)
    agencies = []
    try:
        import sqlite3
        rconn = sqlite3.connect(str(recon_db.RECON_DB_PATH))
        rconn.row_factory = sqlite3.Row
        rows = rconn.execute("""
            SELECT invoice_tenant,
                   COUNT(*) as total_records,
                   SUM(CASE WHEN match_status IN ('full_3way', 'partial_2way') THEN 1 ELSE 0 END) as reconciled,
                   SUM(CASE WHEN match_status = 'full_3way' THEN 1 ELSE 0 END) as full_3way,
                   SUM(CASE WHEN match_status IN ('mismatch', 'invoice_only', 'remittance_only', 'unmatched') THEN 1 ELSE 0 END) as unreconciled,
                   SUM(COALESCE(invoice_amount, 0)) as total_value
            FROM reconciliation_records
            WHERE invoice_tenant IS NOT NULL AND invoice_tenant != ''
            GROUP BY invoice_tenant
            ORDER BY total_value DESC
        """).fetchall()
        rconn.close()
        agencies = [{
            "name": r['invoice_tenant'],
            "count": r['total_records'],
            "total": r['total_value'],
            "reconciled_count": r['reconciled'],
            "full_3way_count": r['full_3way'],
            "unreconciled_count": r['unreconciled'],
        } for r in rows]
    except Exception:
        # Fallback to DB payments if recon not available
        if payments:
            from collections import defaultdict
            by_tenant = defaultdict(lambda: {"count": 0, "total": 0})
            for p in payments:
                t = p["tenant"].replace(".worksuite.com", "")
                by_tenant[t]["count"] += 1
                by_tenant[t]["total"] += float(p.get("total_amount", 0) or 0)
            agencies = sorted(
                [{"name": k, **v} for k, v in by_tenant.items()],
                key=lambda x: x["total"], reverse=True,
            )

    return serialize({
        "payments_count": len(payments),
        "processed_count": len(processed),
        "match_rate": round(match_rate_3way, 1),
        "match_rate_2way": round(match_rate_2way, 1),
        "matched_3way": three_way_matched,
        "matched_2way": two_way_matched,
        "matched": two_way_matched,  # backward compat
        "mismatched": mismatched_count if recon_total > 0 else recon_stats["mismatched"],
        "not_found": recon_summary_data.get('remittance_only', 0) + recon_summary_data.get('invoice_only', 0) if recon_total > 0 else recon_stats["not_found"],
        "unverified": unverified,
        "total_lines": total_to_verify,
        "total_value": recon_stats.get("total_value", 0),
        "total_emails": recon_stats["total_emails"],
        "total_remittances": recon_stats["total_remittances"],
        "agencies": agencies,
        "errors": errors,
        "services": services,
        "sync": {s['source']: s['status'] for s in get_sync_state()},
        "funding_count": recon_summary_data.get('partial_2way', 0) + recon_summary_data.get('full_3way', 0),
    })


# ── Emails ───────────────────────────────────────────────────────────────

@app.get("/api/emails/fetch")
def fetch_emails_endpoint(
    source: str = Query("all", description="oasys|d365_ach|ldn_gss|all"),
    max_results: int = Query(10, ge=1, le=100),
    include_processed: bool = Query(False),
):
    """Fetch remittance emails from Gmail."""
    try:
        if source == "all":
            emails = fetch_all_remittances(max_per_source=max_results)
        else:
            emails = fetch_emails(source, max_results=max_results, include_processed=include_processed)

        results = []
        for e in emails:
            att_names = [a["filename"] for a in e.get("attachments", [])]
            results.append({
                "id": e.get("id"),
                "source": e.get("source", ""),
                "date": e.get("date", "")[:25],
                "subject": e.get("subject", ""),
                "from": e.get("from", ""),
                "attachments": att_names,
                "manual_review": e.get("manual_review", False),
            })

        return {"count": len(results), "emails": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/emails/processed")
def processed_emails(limit: int = Query(100, ge=1, le=500)):
    """Get previously processed emails from local DB."""
    try:
        emails = get_all_emails(limit=limit)
        stats = get_stats()
        return serialize({"emails": emails, "stats": stats})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/emails/{email_id}")
def email_detail(email_id: str):
    """Get full detail for a processed email."""
    detail = get_email_detail(email_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Email not found")
    return serialize(detail)


# ── Pay Runs ─────────────────────────────────────────────────────────────

@app.get("/api/payruns")
def payruns(days: int = Query(30, ge=1, le=365)):
    """Get OMC pay runs from aggregate DB."""
    try:
        runs = get_omc_payruns(days_back=days)
        return serialize({"count": len(runs), "payruns": runs})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)[:100]}")


@app.get("/api/payments")
def payments(days: int = Query(7, ge=1, le=365)):
    """Get OMC payments from aggregate DB."""
    try:
        data = get_omc_payments(days_back=days)
        return serialize({"count": len(data), "payments": data[:500]})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)[:100]}")


@app.get("/api/payments/lookup")
def lookup_payments(nvc_codes: str = Query(..., description="Comma-separated NVC codes")):
    """Look up payments by NVC code."""
    from db_client import lookup_payments_by_nvc
    codes = [c.strip() for c in nvc_codes.split(",") if c.strip()]
    if not codes:
        raise HTTPException(status_code=400, detail="No NVC codes provided")
    try:
        results = lookup_payments_by_nvc(codes)
        return serialize({"results": results, "found": list(results.keys()), "missing": [c for c in codes if c not in results]})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Reconciliation ───────────────────────────────────────────────────────

class ReconcileRequest(BaseModel):
    max_emails: int = 20
    include_processed: bool = False


@app.post("/api/reconcile")
def run_reconciliation(req: ReconcileRequest):
    """Run full reconciliation: fetch emails → parse CSVs → match against DB."""
    try:
        logger.info("=" * 60)
        logger.info("RECONCILIATION RUN STARTED (max=%d, include_processed=%s)", req.max_emails, req.include_processed)

        # Step 1: Fetch emails
        if req.include_processed:
            emails = []
            for key in ["oasys", "d365_ach"]:
                try:
                    batch = fetch_emails(key, max_results=req.max_emails, include_processed=True)
                    emails.extend(batch)
                except Exception as e:
                    logger.warning("Fetch error for %s: %s", key, e)
        else:
            emails = fetch_all_remittances(max_per_source=req.max_emails)

        if not emails:
            processed_count = len(load_processed())
            return {
                "success": True,
                "message": f"No new emails. {processed_count} already processed.",
                "emails_fetched": 0, "remittances_parsed": 0, "reports": [],
            }

        # Store emails
        for e in emails:
            store_email(e)

        # Step 2: Parse CSVs
        all_remittances = []
        manual_count = 0
        for email in emails:
            if email.get("manual_review"):
                manual_count += 1
                continue
            try:
                parsed = parse_email_attachments(email)
                all_remittances.extend(parsed)
            except Exception as e:
                logger.warning("Parse error: %s", e)

        if not all_remittances:
            return {
                "success": True,
                "message": "Emails fetched but no CSVs could be parsed.",
                "emails_fetched": len(emails), "remittances_parsed": 0, "reports": [],
            }

        # Step 3: Reconcile
        reports = reconcile_batch(all_remittances)

        # Store results
        for report in reports:
            eid = report.remittance.source_email_id
            if eid:
                store_reconciliation(eid, report)

        # Step 4: Mark processed
        mark_processed([e["id"] for e in emails])

        # Build response
        report_data = []
        for r in reports:
            matches = []
            for m in r.matches:
                matches.append({
                    "nvc_code": m.nvc_code,
                    "contractor": m.remittance_line.description,
                    "company": m.remittance_line.company,
                    "remittance_amount": float(m.remittance_amount),
                    "db_amount": m.db_amount,
                    "difference": m.difference,
                    "status": m.status,
                    "notes": m.notes,
                    "tenant": m.db_record.get("tenant", "").replace(".worksuite.com", "") if m.db_record else "",
                })
            report_data.append({
                "agency": r.remittance.agency or r.remittance.subject[:40],
                "subject": r.remittance.subject,
                "total": float(r.remittance.payment_amount),
                "source": r.remittance.source_type,
                "matched": r.matched_count,
                "mismatched": r.mismatched_count,
                "not_found": r.not_found_count,
                "total_lines": len(r.matches),
                "matches": matches,
            })

        logger.info("RECONCILIATION COMPLETE: %d reports", len(report_data))
        return serialize({
            "success": True,
            "emails_fetched": len(emails),
            "remittances_parsed": len(all_remittances),
            "manual_review": manual_count,
            "reports": report_data,
        })

    except Exception as e:
        logger.error("Reconciliation failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# ── Tenants ──────────────────────────────────────────────────────────────

@app.get("/api/tenants")
def tenants():
    """Get configured OMC tenants with funding config from DB."""
    import json as _json
    config_path = Path(__file__).parent / "config.json"
    tenant_config = {}
    if config_path.exists():
        cfg = _json.loads(config_path.read_text())
        tenant_config = {t["domain"]: t for t in cfg.get("tenants", [])}

    # Try to enrich with DB funding config
    funding = {}
    try:
        for row in get_tenant_funding_config():
            funding[row["tenant"]] = row
    except Exception as e:
        logger.warning("Could not fetch tenant funding config: %s", e)

    results = []
    for domain in sorted(OMC_TENANTS):
        cfg = tenant_config.get(domain, {})
        fund = funding.get(domain, {})
        results.append({
            "domain": domain,
            "slug": cfg.get("slug", domain.replace(".worksuite.com", "")),
            "display_name": cfg.get("display_name", domain.replace(".worksuite.com", "")),
            "group": cfg.get("group", ""),
            "funding_method": fund.get("funding_method", "unknown"),
            "config_updated": fund.get("updated_at"),
        })

    return {"tenants": results, "count": len(results)}


# ── MoneyCorp Sub-Accounts ───────────────────────────────────────────────

@app.get("/api/moneycorp/subaccounts")
def moneycorp_subaccounts():
    """Get MoneyCorp sub-accounts with latest balances per OMC tenant."""
    try:
        accounts = get_moneycorp_subaccounts()

        # Group by tenant
        by_tenant: dict = {}
        for acct in accounts:
            t = acct["tenant"].replace(".worksuite.com", "")
            if t not in by_tenant:
                by_tenant[t] = {"tenant": t, "processor_id": acct["processor_id"], "currencies": []}
            by_tenant[t]["currencies"].append({
                "currency": acct["currency"],
                "balance": acct["balance"],
                "scheduled": acct["scheduled_amount"],
                "processing": acct["processing_amount"],
                "last_updated": acct.get("last_updated"),
            })

        return serialize({
            "accounts": sorted(by_tenant.values(), key=lambda x: x["tenant"]),
            "count": len(by_tenant),
            "total_currencies": len(accounts),
        })
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)[:100]}")


# ── Config / Meta ────────────────────────────────────────────────────────

@app.get("/api/config")
def config():
    """Get configuration metadata (no secrets)."""
    import os
    import json as _json
    config_path = Path(__file__).parent / "config.json"
    cfg = {}
    if config_path.exists():
        cfg = _json.loads(config_path.read_text())
    return {
        "email_sources": cfg.get("email_sources", {k: v.get("description", k) for k, v in EMAIL_SOURCES.items()}),
        "omc_tenants": sorted([t.replace(".worksuite.com", "") for t in OMC_TENANTS]),
        "gmail_user": os.getenv("GOOGLE_IMPERSONATE_USER", "N/A"),
        "db_name": os.getenv("DB_NAME", "N/A"),
    }


# ── Reconciliation Records (new auto-recon) ─────────────────────────────

@app.get("/api/recon/records")
def recon_records(
    status: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get reconciliation records with filters."""
    records = get_recon_records(status=status, tenant=tenant, search=search,
                                date_from=date_from, date_to=date_to,
                                limit=limit, offset=offset)
    return {"count": len(records), "records": records}


@app.get("/api/recon/summary")
def recon_summary():
    """Get reconciliation summary counts by match_status."""
    return get_recon_summary()


@app.get("/api/recon/record/{nvc_code}")
def recon_record_detail(nvc_code: str):
    """Get single reconciliation record."""
    record = get_recon_record(nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")
    return record


# ── Queue & Cross-Search ─────────────────────────────────────────────────

@app.get("/api/recon/queue")
def recon_queue(
    status: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    flag: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("last_updated_at"),
    sort_dir: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get reconciliation queue — unreconciled records sorted by priority."""
    import sqlite3
    conn = sqlite3.connect(str(recon_db.RECON_DB_PATH))
    conn.row_factory = sqlite3.Row

    conditions = []
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

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Count
    total = conn.execute(f"SELECT COUNT(*) FROM reconciliation_records {where}", params).fetchone()[0]

    # Sort
    allowed_sorts = {"last_updated_at", "first_seen_at", "invoice_amount", "remittance_amount", "funding_amount"}
    sort_col = sort_by if sort_by in allowed_sorts else "last_updated_at"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    # Priority ordering: mismatch first, then partial, then single-source
    order = f"""
        CASE match_status
            WHEN 'mismatch' THEN 1
            WHEN 'remittance_only' THEN 2
            WHEN 'invoice_only' THEN 3
            WHEN 'unmatched' THEN 4
            WHEN 'partial_2way' THEN 5
            WHEN 'full_3way' THEN 6
            WHEN 'resolved' THEN 7
        END, {sort_col} {direction}
    """

    rows = conn.execute(
        f"SELECT * FROM reconciliation_records {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return {"records": [dict(r) for r in rows], "total": total}


@app.get("/api/search/cross")
def cross_search(
    q: str = Query(""),
    source: str = Query("invoices"),
    amount_min: Optional[float] = Query(None),
    amount_max: Optional[float] = Query(None),
    tenant: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    """Cross-search: find records in a specific source for manual association."""
    import sqlite3
    conn = sqlite3.connect(str(recon_db.RECON_DB_PATH))
    conn.row_factory = sqlite3.Row

    conditions = []
    params: list = []

    if source == "invoices":
        conditions.append("invoice_amount IS NOT NULL")
        if q:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{q}%")
        if amount_min is not None:
            conditions.append("invoice_amount >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("invoice_amount <= ?")
            params.append(amount_max)
        if tenant:
            conditions.append("invoice_tenant LIKE ?")
            params.append(f"%{tenant}%")
    elif source == "funding":
        conditions.append("funding_amount IS NOT NULL")
        if q:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{q}%")
        if amount_min is not None:
            conditions.append("funding_amount >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("funding_amount <= ?")
            params.append(amount_max)
    elif source == "emails":
        conditions.append("remittance_amount IS NOT NULL")
        if q:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{q}%")
        if amount_min is not None:
            conditions.append("remittance_amount >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("remittance_amount <= ?")
            params.append(amount_max)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM reconciliation_records {where} ORDER BY last_updated_at DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()
    return {"results": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/recon/associate")
def recon_associate(body: dict):
    """Manually associate two records — merge data from one NVC into another."""
    import sqlite3
    from datetime import datetime

    nvc = body.get("nvc_code", "")
    target = body.get("associate_with", "")
    source = body.get("source", "")
    notes = body.get("notes", "")

    if not nvc or not target:
        raise HTTPException(status_code=400, detail="nvc_code and associate_with required")

    conn = sqlite3.connect(str(recon_db.RECON_DB_PATH))
    conn.row_factory = sqlite3.Row

    target_row = conn.execute("SELECT * FROM reconciliation_records WHERE nvc_code = ?", (target,)).fetchone()
    if not target_row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Target NVC {target} not found")

    target_row = dict(target_row)
    now = datetime.now().isoformat()

    # Merge the missing data from target into the primary record
    updates = {"last_updated_at": now, "notes": f"Manually associated with {target}. {notes}".strip()}
    if source == "invoices" and target_row.get("invoice_amount"):
        updates.update({
            "invoice_amount": target_row["invoice_amount"],
            "invoice_status": target_row["invoice_status"],
            "invoice_tenant": target_row["invoice_tenant"],
            "invoice_payrun_ref": target_row["invoice_payrun_ref"],
            "invoice_currency": target_row["invoice_currency"],
        })
    elif source == "funding" and target_row.get("funding_amount"):
        updates.update({
            "funding_amount": target_row["funding_amount"],
            "funding_account_id": target_row["funding_account_id"],
            "funding_date": target_row["funding_date"],
        })
    elif source == "emails" and target_row.get("remittance_amount"):
        updates.update({
            "remittance_amount": target_row["remittance_amount"],
            "remittance_date": target_row["remittance_date"],
            "remittance_source": target_row["remittance_source"],
            "remittance_email_id": target_row["remittance_email_id"],
        })

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE reconciliation_records SET {set_clause} WHERE nvc_code = ?",
        list(updates.values()) + [nvc]
    )
    conn.commit()
    conn.close()

    # Recalculate match status
    recon_db.recalculate_match_status(nvc)

    record = recon_db.get_recon_record(nvc)
    return {"success": True, "record": record}


@app.post("/api/recon/flag")
def recon_flag(body: dict):
    """Flag a record for follow-up."""
    import sqlite3
    from datetime import datetime

    nvc = body.get("nvc_code", "")
    flag = body.get("flag", "")
    notes = body.get("notes", "")

    if not nvc:
        raise HTTPException(status_code=400, detail="nvc_code required")

    conn = sqlite3.connect(str(recon_db.RECON_DB_PATH))

    # Add columns if they don't exist
    try:
        conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE reconciliation_records ADD COLUMN flag_notes TEXT")
    except Exception:
        pass

    now = datetime.now().isoformat()

    if flag == "resolved":
        conn.execute(
            "UPDATE reconciliation_records SET flag = ?, flag_notes = ?, resolved_at = ?, resolved_by = ?, last_updated_at = ? WHERE nvc_code = ?",
            (flag, notes, now, "ops_user", now, nvc)
        )
    else:
        conn.execute(
            "UPDATE reconciliation_records SET flag = ?, flag_notes = ?, last_updated_at = ? WHERE nvc_code = ?",
            (flag, notes, now, nvc)
        )

    conn.commit()
    conn.close()

    if flag == "resolved":
        recon_db.recalculate_match_status(nvc)

    return {"success": True}


# ── Sync Control ─────────────────────────────────────────────────────────

@app.post("/api/sync/trigger")
def trigger_sync():
    """Trigger an immediate sync cycle."""
    try:
        from sync_service import run_sync_cycle
        results = run_sync_cycle()
        return serialize({"success": True, "results": results})
    except Exception as e:
        logger.error("Manual sync trigger failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync/status")
def sync_status():
    """Get sync state for all sources."""
    return {"sources": get_sync_state()}


# ── Cached Pay Runs ──────────────────────────────────────────────────────

@app.get("/api/payruns/cached")
def cached_payruns(
    tenant: Optional[str] = Query(None),
    status: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get locally cached pay runs."""
    runs = get_cached_payruns(tenant=tenant, status=status, date_from=date_from,
                              date_to=date_to, search=search, sort_by=sort_by,
                              sort_dir=sort_dir, limit=limit, offset=offset)
    return serialize({"count": len(runs), "payruns": runs})


# ── Cross-Search ─────────────────────────────────────────────────────────

@app.get("/api/search/cross")
def cross_search(
    q: Optional[str] = Query(None),
    source: str = Query("invoices", description="emails|invoices|funding"),
    amount_min: Optional[float] = Query(None),
    amount_max: Optional[float] = Query(None),
    tenant: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Cross-search across emails, invoices, and funding records."""
    results = []

    if source == "emails":
        # Search processed_emails.db
        try:
            import sqlite3 as _sq
            econn = _sq.connect(str(Path('data/processed_emails.db')))
            econn.row_factory = _sq.Row
            conditions = []
            params = []
            if q:
                conditions.append("(e.subject LIKE ? OR e.sender LIKE ? OR mr.nvc_code LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = econn.execute(f"""
                SELECT DISTINCT mr.nvc_code, mr.remittance_amount, mr.description, mr.company,
                       mr.status, mr.tenant, e.subject, e.sender, e.email_date,
                       r.agency, r.payment_amount, r.source_type
                FROM match_results mr
                JOIN remittances r ON mr.remittance_id = r.id
                JOIN emails e ON r.email_id = e.id
                {where}
                ORDER BY e.email_date DESC
                LIMIT ?
            """, params + [limit]).fetchall()
            econn.close()
            for r in rows:
                rd = dict(r)
                amt = rd.get('remittance_amount', 0) or 0
                if amount_min and amt < amount_min:
                    continue
                if amount_max and amt > amount_max:
                    continue
                results.append({
                    "source": "email",
                    "nvc_code": rd.get("nvc_code"),
                    "amount": amt,
                    "description": rd.get("description"),
                    "company": rd.get("company"),
                    "tenant": rd.get("tenant"),
                    "email_subject": rd.get("subject"),
                    "sender": rd.get("sender"),
                    "date": rd.get("email_date"),
                    "agency": rd.get("agency"),
                })
        except Exception as e:
            logger.warning("Email cross-search failed: %s", e)

    elif source == "invoices":
        conn = __import__('sqlite3').connect(str(recon_db.RECON_DB_PATH))
        conn.row_factory = __import__('sqlite3').Row
        conditions = ["invoice_amount IS NOT NULL"]
        params = []
        if q:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{q}%")
        if tenant:
            conditions.append("invoice_tenant LIKE ?")
            params.append(f"%{tenant}%")
        if amount_min is not None:
            conditions.append("invoice_amount >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("invoice_amount <= ?")
            params.append(amount_max)
        where = f"WHERE {' AND '.join(conditions)}"
        rows = conn.execute(
            f"SELECT * FROM reconciliation_records {where} ORDER BY last_updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()
        results = [{"source": "invoice", **dict(r)} for r in rows]

    elif source == "funding":
        conn = __import__('sqlite3').connect(str(recon_db.RECON_DB_PATH))
        conn.row_factory = __import__('sqlite3').Row
        conditions = ["funding_amount IS NOT NULL"]
        params = []
        if q:
            conditions.append("nvc_code LIKE ?")
            params.append(f"%{q}%")
        if amount_min is not None:
            conditions.append("funding_amount >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("funding_amount <= ?")
            params.append(amount_max)
        where = f"WHERE {' AND '.join(conditions)}"
        rows = conn.execute(
            f"SELECT * FROM reconciliation_records {where} ORDER BY last_updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()
        results = [{"source": "funding", **dict(r)} for r in rows]

    return {"count": len(results), "results": serialize(results)}


# ── Suggested Matches ────────────────────────────────────────────────────

@app.get("/api/recon/suggestions/{nvc_code}")
def recon_suggestions(nvc_code: str):
    """Find potential matches for a given NVC code."""
    record = recon_db.get_recon_record(nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")

    import sqlite3 as _sq
    conn = _sq.connect(str(recon_db.RECON_DB_PATH))
    conn.row_factory = _sq.Row

    suggestions = []
    seen = set()

    # 1. Amount-based matches (±1%)
    for amt_field, src_label in [
        ('remittance_amount', 'remittance'),
        ('invoice_amount', 'invoice'),
        ('funding_amount', 'funding'),
    ]:
        amt = record.get(amt_field)
        if amt is None:
            continue
        tolerance = amt * 0.01
        lo, hi = amt - tolerance, amt + tolerance
        # Find other records that have a DIFFERENT source with matching amount
        for other_field, other_label in [
            ('remittance_amount', 'remittance'),
            ('invoice_amount', 'invoice'),
            ('funding_amount', 'funding'),
        ]:
            if other_label == src_label:
                continue
            # Only suggest if the target record is missing this source
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
                # Boost if tenant matches
                if record.get('invoice_tenant') and r['invoice_tenant'] == record.get('invoice_tenant'):
                    confidence += 0.15
                suggestions.append({
                    "nvc_code": rk,
                    "reason": f"Amount match ({other_label}: {r[other_field]:.2f})",
                    "confidence": round(confidence, 2),
                    "record": dict(r),
                })

    # 2. Fuzzy NVC code match (prefix)
    if len(nvc_code) > 4:
        prefix = nvc_code[:len(nvc_code)-2]
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
                "nvc_code": rk,
                "reason": f"Similar NVC code ({rk})",
                "confidence": 0.5,
                "record": dict(r),
            })

    conn.close()

    # Sort by confidence, top 5
    suggestions.sort(key=lambda x: x['confidence'], reverse=True)
    return {"nvc_code": nvc_code, "suggestions": serialize(suggestions[:5])}


# ── Manual Association ───────────────────────────────────────────────────

class AssociateRequest(BaseModel):
    nvc_code: str
    associate_with: str
    source: str  # invoice|funding|remittance
    notes: str = ""

@app.post("/api/recon/associate")
def recon_associate(req: AssociateRequest):
    """Manually associate two records by merging data."""
    target = recon_db.get_recon_record(req.nvc_code)
    donor = recon_db.get_recon_record(req.associate_with)
    if not target:
        raise HTTPException(status_code=404, detail=f"Target {req.nvc_code} not found")
    if not donor:
        raise HTTPException(status_code=404, detail=f"Source {req.associate_with} not found")

    # Merge the specified source from donor into target
    if req.source == "remittance" and donor.get('remittance_amount') is not None:
        recon_db.upsert_from_remittance(
            req.nvc_code, donor['remittance_amount'],
            donor.get('remittance_date', ''), donor.get('remittance_source', ''),
            donor.get('remittance_email_id', '')
        )
    elif req.source == "invoice" and donor.get('invoice_amount') is not None:
        recon_db.upsert_from_invoice(
            req.nvc_code, donor['invoice_amount'],
            donor.get('invoice_status', ''), donor.get('invoice_tenant', ''),
            donor.get('invoice_payrun_ref', ''), donor.get('invoice_currency', '')
        )
    elif req.source == "funding" and donor.get('funding_amount') is not None:
        recon_db.upsert_from_funding(
            req.nvc_code, donor['funding_amount'],
            donor.get('funding_account_id', ''), donor.get('funding_date', '')
        )
    else:
        raise HTTPException(status_code=400, detail=f"No {req.source} data in {req.associate_with}")

    # Add audit note
    import sqlite3 as _sq
    conn = _sq.connect(str(recon_db.RECON_DB_PATH))
    now = datetime.now().isoformat()
    existing_notes = target.get('notes') or ''
    audit = f"[{now}] Associated {req.source} from {req.associate_with}. {req.notes}"
    new_notes = f"{existing_notes}\n{audit}".strip()
    conn.execute("UPDATE reconciliation_records SET notes = ?, last_updated_at = ? WHERE nvc_code = ?",
                 (new_notes, now, req.nvc_code))
    conn.commit()
    conn.close()

    updated = recon_db.get_recon_record(req.nvc_code)
    return {"success": True, "record": serialize(updated)}


# ── Flag for Follow-up ───────────────────────────────────────────────────

class FlagRequest(BaseModel):
    nvc_code: str
    flag: str  # needs_outreach|investigating|escalated
    notes: str = ""

@app.post("/api/recon/flag")
def recon_flag(req: FlagRequest):
    """Flag a record for follow-up."""
    allowed_flags = {'needs_outreach', 'investigating', 'escalated', ''}
    if req.flag and req.flag not in allowed_flags:
        raise HTTPException(status_code=400, detail=f"Invalid flag. Use: {allowed_flags}")

    record = recon_db.get_recon_record(req.nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")

    import sqlite3 as _sq
    conn = _sq.connect(str(recon_db.RECON_DB_PATH))
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE reconciliation_records SET flag = ?, flag_notes = ?, last_updated_at = ? WHERE nvc_code = ?",
        (req.flag or None, req.notes or None, now, req.nvc_code)
    )
    conn.commit()
    conn.close()

    updated = recon_db.get_recon_record(req.nvc_code)
    return {"success": True, "record": serialize(updated)}


# ── Reconciliation Queue ─────────────────────────────────────────────────

@app.get("/api/recon/queue")
def recon_queue(
    status: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    flag: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: str = Query("priority"),
    sort_dir: str = Query("asc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get unreconciled records as a prioritized work queue."""
    records, total = recon_db.get_recon_records_queue(
        status=status, tenant=tenant, flag=flag, search=search,
        date_from=date_from, date_to=date_to, sort_by=sort_by,
        sort_dir=sort_dir, limit=limit, offset=offset,
    )
    return serialize({"total": total, "count": len(records), "records": records})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
