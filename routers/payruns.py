"""Pay run and payment routes."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from routers import serialize
from db_client import get_omc_payments, get_omc_payruns
from recon_db import get_cached_payruns, get_cached_invoices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["payruns"])


@router.get("/payruns")
def payruns(days: int = Query(30, ge=1, le=365)):
    """Get OMC pay runs from aggregate DB."""
    try:
        runs = get_omc_payruns(days_back=days)
        return serialize({"count": len(runs), "payruns": runs})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)[:100]}")


@router.get("/payments")
def payments(days: int = Query(7, ge=1, le=365)):
    """Get OMC payments from aggregate DB."""
    try:
        data = get_omc_payments(days_back=days)
        return serialize({"count": len(data), "payments": data[:500]})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)[:100]}")


@router.get("/payments/lookup")
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


@router.get("/payruns/cached")
def cached_payruns_endpoint(
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


@router.get("/invoices/cached")
def cached_invoices_endpoint(
    tenant: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get locally cached invoices."""
    invoices, total = get_cached_invoices(tenant=tenant, status=status, search=search,
                                          sort_by=sort_by, sort_dir=sort_dir,
                                          limit=limit, offset=offset)
    return serialize({"invoices": invoices, "total": total})
