"""FastAPI backend for OMC Funding Tracker.

Thin app shell — all endpoint logic lives in routers/.
"""
import asyncio
import logging
import os
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from routers import serialize
from routers.core import router as core_router
from routers.emails import router as emails_router
from routers.payruns import router as payruns_router
from routers.recon import router as recon_router
from routers.received_payments import router as received_payments_router
from routers.sync import router as sync_router
from routers.search import router as search_router

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


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(periodic_sync())
    yield
    task.cancel()


# ── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OMC Funding Tracker API",
    version="2.1.0",
    description="Omnicom Pay Run Funding — Remittance / DB / MoneyCorp reconciliation",
    lifespan=lifespan,
)

_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001,http://localhost:3002")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(core_router)
app.include_router(emails_router)
app.include_router(payruns_router)
app.include_router(recon_router)
app.include_router(received_payments_router)
app.include_router(sync_router)
app.include_router(search_router)


# ── Reconcile (top-level, not under /api/recon) ──────────────────────────

class ReconcileRequest(BaseModel):
    max_emails: int = 20
    include_processed: bool = False


@app.post("/api/reconcile")
def run_reconciliation(req: ReconcileRequest):
    """Run full reconciliation: fetch emails -> parse CSVs -> match against DB."""
    from reconciliation_service import run_pipeline, format_report_data
    try:
        result = run_pipeline(
            max_emails=req.max_emails,
            include_processed=req.include_processed,
        )
        return serialize({
            "success": result.success,
            "message": result.message,
            "emails_fetched": result.emails_fetched,
            "remittances_parsed": result.remittances_parsed,
            "manual_review": result.manual_review,
            "reports": format_report_data(result.reports),
        })
    except Exception as e:
        logger.error("Reconciliation failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
