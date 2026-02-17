"""Sync control routes."""
import logging

from fastapi import APIRouter, HTTPException

from routers import serialize
from recon_db import get_sync_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/trigger")
def trigger_sync():
    """Trigger an immediate sync cycle."""
    try:
        from sync_service import run_sync_cycle
        results = run_sync_cycle()
        return serialize({"success": True, "results": results})
    except Exception as e:
        logger.error("Manual sync trigger failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
def sync_status():
    """Get sync state for all sources."""
    return {"sources": get_sync_state()}
