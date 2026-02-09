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
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from db_client import get_omc_payments, get_omc_payruns, status_label, OMC_TENANTS
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OMC Funding Tracker API",
    version="2.0.0",
    description="Omnicom Pay Run Funding — Remittance ↔ DB ↔ MoneyCorp reconciliation (READ-ONLY)",
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
    two_way_matched = recon_stats["matched"]
    moneycorp_verified = 0  # TODO: populate when MoneyCorp API is integrated
    three_way_matched = moneycorp_verified
    
    # Total remittance lines that should be verified
    total_to_verify = recon_stats.get("total_matches", total_lines) or total_lines
    
    # 3-way match rate (0% until MoneyCorp is integrated)
    match_rate_3way = (three_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    # 2-way match rate (remittance ↔ Worksuite only)  
    match_rate_2way = (two_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    
    # Issues = anything not fully 3-way verified
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
        "mismatched": recon_stats["mismatched"],
        "not_found": recon_stats["not_found"],
        "unverified": unverified,
        "total_lines": total_to_verify,
        "total_value": recon_stats.get("total_value", 0),
        "total_emails": recon_stats["total_emails"],
        "total_remittances": recon_stats["total_remittances"],
        "agencies": agencies,
        "errors": errors,
        "services": services,
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


# ── Config / Meta ────────────────────────────────────────────────────────

@app.get("/api/config")
def config():
    """Get configuration metadata (no secrets)."""
    import os
    return {
        "email_sources": {k: v.get("description", k) for k, v in EMAIL_SOURCES.items()},
        "omc_tenants": sorted([t.replace(".worksuite.com", "") for t in OMC_TENANTS]),
        "gmail_user": os.getenv("GOOGLE_IMPERSONATE_USER", "N/A"),
        "db_name": os.getenv("DB_NAME", "N/A"),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
