"""Email routes: fetch, processed, detail."""
import logging

from fastapi import APIRouter, HTTPException, Query

from routers import serialize
from gmail_client import fetch_all_remittances, fetch_emails
from email_db import get_all_emails, get_email_detail, get_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.get("/fetch")
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


@router.get("/processed")
def processed_emails(limit: int = Query(100, ge=1, le=500)):
    """Get previously processed emails from local DB."""
    try:
        emails = get_all_emails(limit=limit)
        stats = get_stats()
        return serialize({"emails": emails, "stats": stats})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{email_id}")
def email_detail(email_id: str):
    """Get full detail for a processed email."""
    detail = get_email_detail(email_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Email not found")
    return serialize(detail)
