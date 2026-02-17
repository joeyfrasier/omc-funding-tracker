"""Reconciliation routes: records, queue, suggestions, associate, flag."""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from routers import serialize
import recon_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recon", tags=["reconciliation"])


# ── Records ──────────────────────────────────────────────────────────────

@router.get("/records")
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
    records = recon_db.get_recon_records(status=status, tenant=tenant, search=search,
                                         date_from=date_from, date_to=date_to,
                                         limit=limit, offset=offset)
    return {"count": len(records), "records": records}


@router.get("/summary")
def recon_summary():
    """Get reconciliation summary counts by match_status."""
    return recon_db.get_recon_summary()


@router.get("/record/{nvc_code}")
def recon_record_detail(nvc_code: str):
    """Get single reconciliation record."""
    record = recon_db.get_recon_record(nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")
    return record


# ── Queue ────────────────────────────────────────────────────────────────

@router.get("/queue")
def recon_queue(
    status: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    flag: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    invoice_status: Optional[str] = Query(None),
    sort_by: str = Query("last_updated_at"),
    sort_dir: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get reconciliation queue — records sorted by priority."""
    records, total = recon_db.get_recon_queue(
        status=status, tenant=tenant, flag=flag, search=search,
        invoice_status=invoice_status, sort_by=sort_by, sort_dir=sort_dir,
        limit=limit, offset=offset,
    )
    return {"records": records, "total": total}


# ── Suggestions ──────────────────────────────────────────────────────────

@router.get("/suggestions/{nvc_code}")
def recon_suggestions(nvc_code: str):
    """Find potential matches for a given NVC code."""
    record = recon_db.get_recon_record(nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")

    suggestions = recon_db.find_amount_suggestions(nvc_code)
    return {"nvc_code": nvc_code, "suggestions": serialize(suggestions)}


# ── Manual Association ───────────────────────────────────────────────────

class AssociateRequest(BaseModel):
    nvc_code: str
    associate_with: str
    source: str  # invoice|funding|remittance
    notes: str = ""


@router.post("/associate")
def recon_associate(req: AssociateRequest):
    """Manually associate two records by merging data."""
    target = recon_db.get_recon_record(req.nvc_code)
    donor = recon_db.get_recon_record(req.associate_with)
    if not target:
        raise HTTPException(status_code=404, detail=f"Target {req.nvc_code} not found")
    if not donor:
        raise HTTPException(status_code=404, detail=f"Source {req.associate_with} not found")

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
    elif req.source == "funding" and donor.get('payment_amount') is not None:
        recon_db.upsert_from_payment(
            req.nvc_code, donor['payment_amount'],
            donor.get('payment_account_id', ''), donor.get('payment_date', '')
        )
    else:
        raise HTTPException(status_code=400, detail=f"No {req.source} data in {req.associate_with}")

    audit = f"[{datetime.now().isoformat()}] Associated {req.source} from {req.associate_with}. {req.notes}"
    recon_db.append_recon_note(req.nvc_code, audit)

    updated = recon_db.get_recon_record(req.nvc_code)
    return {"success": True, "record": serialize(updated)}


# ── Flag for Follow-up ───────────────────────────────────────────────────

class FlagRequest(BaseModel):
    nvc_code: str
    flag: str  # needs_outreach|investigating|escalated
    notes: str = ""


@router.post("/flag")
def recon_flag(req: FlagRequest):
    """Flag a record for follow-up."""
    allowed_flags = {'needs_outreach', 'investigating', 'escalated', ''}
    if req.flag and req.flag not in allowed_flags:
        raise HTTPException(status_code=400, detail=f"Invalid flag. Use: {allowed_flags}")

    record = recon_db.get_recon_record(req.nvc_code)
    if not record:
        raise HTTPException(status_code=404, detail="NVC code not found")

    recon_db.update_recon_flag(req.nvc_code, req.flag, req.notes)

    updated = recon_db.get_recon_record(req.nvc_code)
    return {"success": True, "record": serialize(updated)}
