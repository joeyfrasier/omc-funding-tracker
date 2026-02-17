"""Cross-search routes."""
import logging
from typing import Optional

from fastapi import APIRouter, Query

from routers import serialize
from email_db import search_email_matches
from recon_db import search_recon_records

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/cross")
def cross_search(
    q: Optional[str] = Query(None),
    source: str = Query("invoices", description="emails|invoices|funding"),
    amount_min: Optional[float] = Query(None),
    amount_max: Optional[float] = Query(None),
    tenant: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Cross-search across emails, invoices, and funding records."""
    if source == "emails":
        results = search_email_matches(query=q, amount_min=amount_min, amount_max=amount_max, limit=limit)
    elif source == "invoices":
        rows = search_recon_records(
            amount_field='invoice_amount', nvc_search=q, tenant=tenant,
            amount_min=amount_min, amount_max=amount_max, limit=limit,
        )
        results = [{"source": "invoice", **r} for r in rows]
    elif source == "funding":
        rows = search_recon_records(
            amount_field='payment_amount', nvc_search=q,
            amount_min=amount_min, amount_max=amount_max, limit=limit,
        )
        results = [{"source": "funding", **r} for r in rows]
    else:
        results = []

    return {"count": len(results), "results": serialize(results)}
