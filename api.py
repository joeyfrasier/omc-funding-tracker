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
            run_sync_cycle()
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

    # Agency breakdown
    agencies = []
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
