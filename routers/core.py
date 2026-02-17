"""Core routes: health, config, tenants, moneycorp, overview."""
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from routers import serialize
from db_client import (
    get_omc_payments, OMC_TENANTS,
    get_moneycorp_subaccounts, get_tenant_funding_config,
)
from gmail_client import load_processed, EMAIL_SOURCES
from email_db import get_stats
from recon_db import get_recon_summary, get_sync_state, get_agency_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["core"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "omc-funding-tracker", "version": "2.1.0"}


@router.get("/overview")
def overview(days: int = Query(7, ge=1, le=365)):
    """Dashboard overview: payments + reconciliation stats.

    Resilient: runs DB/Gmail checks in parallel with a 12s timeout.
    Returns partial data if any service is unreachable.
    """
    errors = {}
    services = {}
    payments = []
    recon_stats = {
        "total_emails": 0, "total_remittances": 0,
        "matched": 0, "mismatched": 0, "not_found": 0, "total_value": 0,
    }

    try:
        recon_stats = get_stats()
    except Exception:
        pass

    recon_summary_data = {}
    try:
        recon_summary_data = get_recon_summary()
    except Exception:
        pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        db_future = executor.submit(get_omc_payments, days_back=days)
        gmail_future = executor.submit(load_processed)

        try:
            payments = db_future.result(timeout=12)
            services["db"] = "connected"
        except Exception as e:
            errors["db"] = str(e)[:100]
            services["db"] = "unreachable"

        try:
            gmail_future.result(timeout=12)
            services["gmail"] = "connected"
        except Exception as e:
            errors["gmail"] = str(e)[:100]
            services["gmail"] = "unreachable"

    total_issues = recon_stats["mismatched"] + recon_stats["not_found"]
    total_lines = recon_stats["matched"] + total_issues

    recon_total = recon_summary_data.get('total', 0)
    if recon_total > 0:
        three_way_matched = recon_summary_data.get('full_4way', 0)
        two_way_matched = (recon_summary_data.get('2way_matched', 0) +
                           recon_summary_data.get('3way_awaiting_payment', 0) +
                           recon_summary_data.get('3way_no_funding', 0) +
                           three_way_matched)
        total_to_verify = recon_total
        mismatched_count = recon_summary_data.get('amount_mismatch', 0)
    else:
        two_way_matched = recon_stats["matched"]
        three_way_matched = 0
        total_to_verify = recon_stats.get("total_matches", total_lines) or total_lines
        mismatched_count = recon_stats["mismatched"]

    match_rate_3way = (three_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    match_rate_2way = (two_way_matched / total_to_verify * 100) if total_to_verify > 0 else 0
    unverified = total_to_verify - three_way_matched

    # Agency breakdown
    try:
        agencies = get_agency_stats()
    except Exception:
        if payments:
            by_tenant = defaultdict(lambda: {"count": 0, "total": 0})
            for p in payments:
                t = p["tenant"].replace(".worksuite.com", "")
                by_tenant[t]["count"] += 1
                by_tenant[t]["total"] += float(p.get("total_amount", 0) or 0)
            agencies = sorted(
                [{"name": k, **v} for k, v in by_tenant.items()],
                key=lambda x: x["total"], reverse=True,
            )
        else:
            agencies = []

    return serialize({
        "payments_count": len(payments),
        "processed_count": 0,
        "match_rate": round(match_rate_3way, 1),
        "match_rate_2way": round(match_rate_2way, 1),
        "matched_3way": three_way_matched,
        "matched_2way": two_way_matched,
        "matched": two_way_matched,
        "mismatched": mismatched_count if recon_total > 0 else recon_stats["mismatched"],
        "not_found": (recon_summary_data.get('remittance_only', 0) + recon_summary_data.get('invoice_only', 0))
                     if recon_total > 0 else recon_stats["not_found"],
        "unverified": unverified,
        "total_lines": total_to_verify,
        "total_value": recon_stats.get("total_value", 0),
        "total_emails": recon_stats["total_emails"],
        "total_remittances": recon_stats["total_remittances"],
        "agencies": agencies,
        "errors": errors,
        "services": services,
        "sync": {s['source']: s['status'] for s in get_sync_state()},
        "funding_count": recon_summary_data.get('3way_awaiting_payment', 0) + recon_summary_data.get('full_4way', 0),
    })


@router.get("/tenants")
def tenants():
    """Get configured OMC tenants with funding config from DB."""
    import json as _json
    config_path = Path(__file__).parent.parent / "config.json"
    tenant_config = {}
    if config_path.exists():
        cfg = _json.loads(config_path.read_text())
        tenant_config = {t["domain"]: t for t in cfg.get("tenants", [])}

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


@router.get("/moneycorp/subaccounts")
def moneycorp_subaccounts():
    """Get MoneyCorp sub-accounts with latest balances per OMC tenant."""
    from fastapi import HTTPException
    try:
        accounts = get_moneycorp_subaccounts()
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


@router.get("/config")
def config():
    """Get configuration metadata (no secrets)."""
    import json as _json
    config_path = Path(__file__).parent.parent / "config.json"
    cfg = {}
    if config_path.exists():
        cfg = _json.loads(config_path.read_text())
    return {
        "email_sources": cfg.get("email_sources", {k: v.get("description", k) for k, v in EMAIL_SOURCES.items()}),
        "omc_tenants": sorted([t.replace(".worksuite.com", "") for t in OMC_TENANTS]),
        "gmail_user": os.getenv("GOOGLE_IMPERSONATE_USER", "N/A"),
        "db_name": os.getenv("DB_NAME", "N/A"),
    }
