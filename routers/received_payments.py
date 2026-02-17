"""Received payments routes (Leg 3 — Inbound Funding)."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from routers import serialize
import recon_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/received-payments", tags=["received_payments"])


@router.get("")
def list_received_payments(
    account_id: Optional[str] = Query(None),
    match_status: Optional[str] = Query(None),
    payer: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List received payments with filters."""
    records, total = recon_db.get_received_payments(
        account_id=account_id, match_status=match_status, payer=payer,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    return serialize({"records": records, "total": total})


@router.get("/summary")
def received_payments_summary():
    """Get received payments summary."""
    return serialize(recon_db.get_received_payments_summary())


@router.get("/suggestions/{payment_id}")
def received_payment_suggestions(payment_id: str):
    """Get suggested remittance email matches for a received payment."""
    rp = recon_db.get_received_payment(payment_id)
    if not rp:
        raise HTTPException(status_code=404, detail="Received payment not found")

    email_totals = recon_db.get_email_remittance_totals()
    suggestions = []
    for et in email_totals:
        score = 0.0
        rp_amt = rp['amount']
        em_amt = et['total_amount']
        if em_amt and rp_amt:
            diff_pct = abs(rp_amt - em_amt) / max(rp_amt, em_amt) if max(rp_amt, em_amt) > 0 else 1
            if diff_pct <= 0.001:
                score += 0.5
            elif diff_pct <= 0.01:
                score += 0.35
            elif diff_pct <= 0.05:
                score += 0.15
            elif diff_pct <= 0.1:
                score += 0.05

        if score > 0:
            suggestions.append({
                'email_id': et['remittance_email_id'],
                'total_amount': et['total_amount'],
                'date': et['date'],
                'nvc_count': et['nvc_count'],
                'score': round(score, 3),
            })

    suggestions.sort(key=lambda x: x['score'], reverse=True)
    return serialize({"payment_id": payment_id, "suggestions": suggestions[:10]})


@router.get("/{payment_id}")
def received_payment_detail(payment_id: str):
    """Get single received payment detail."""
    record = recon_db.get_received_payment(payment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Received payment not found")
    return serialize(record)


class MatchReceivedPaymentRequest(BaseModel):
    email_id: str
    confidence: float = 1.0
    method: str = "manual"


@router.post("/{payment_id}/match")
def match_received_payment_endpoint(payment_id: str, req: MatchReceivedPaymentRequest):
    """Manually match a received payment to a remittance email."""
    rp = recon_db.get_received_payment(payment_id)
    if not rp:
        raise HTTPException(status_code=404, detail="Received payment not found")

    recon_db.match_received_payment(payment_id, req.email_id, req.confidence, req.method, 'manual')

    nvc_codes = recon_db.get_nvc_codes_for_email(req.email_id)
    for nvc in nvc_codes:
        recon_db.link_received_payment_to_nvc(
            nvc, payment_id, rp['amount'], rp.get('payment_date', '')
        )

    return {"success": True, "linked_nvcs": len(nvc_codes)}


@router.post("/{payment_id}/unmatch")
def unmatch_received_payment_endpoint(payment_id: str):
    """Undo a received payment match."""
    rp = recon_db.get_received_payment(payment_id)
    if not rp:
        raise HTTPException(status_code=404, detail="Received payment not found")
    recon_db.unmatch_received_payment(payment_id)
    return {"success": True}
