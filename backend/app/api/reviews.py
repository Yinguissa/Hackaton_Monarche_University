from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.auth import require_auth
from app.database.connection import get_session, session_scope
from app.models.email import Email
from app.models.discrepancy import ComparisonResult
from app.models.review import Review
from app.services.processing import process_email_job

router = APIRouter(prefix="/reviews", tags=["reviews"], dependencies=[Depends(require_auth)])


@router.get("")
def list_reviews(session: Session = Depends(get_session)):
    """Pending human-review queue: every NEEDS_REVIEW email that doesn't yet
    have a resolved Review row."""
    needs_review = session.exec(
        select(Email).where(Email.status == "NEEDS_REVIEW")
    ).all()
    out = []
    for email in needs_review:
        cr = session.exec(
            select(ComparisonResult).where(ComparisonResult.email_id == email.email_id)
        ).first()
        existing_review = session.exec(
            select(Review)
            .where(Review.email_id == email.email_id)
            .where(Review.resolved_at.is_(None))
        ).first()
        out.append({
            "email_id": email.email_id,
            "subject": email.subject,
            "reason": cr.review_reason if cr else None,
            "review_id": existing_review.id if existing_review else None,
        })
    return out


class ReviewDecision(BaseModel):
    decision: str          # approve | correct | retry
    corrected_status: Optional[str] = None      # used when decision == "correct"
    corrected_defect_fields: Optional[List[str]] = None


@router.post("/{email_id}")
def submit_review(email_id: str, payload: ReviewDecision, session: Session = Depends(get_session)):
    email = session.exec(select(Email).where(Email.email_id == email_id)).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    cr = session.exec(
        select(ComparisonResult).where(ComparisonResult.email_id == email_id)
    ).first()

    if payload.decision not in ("approve", "correct", "retry"):
        raise HTTPException(status_code=400, detail="decision must be approve|correct|retry")

    review = Review(email_id=email_id, reason=cr.review_reason if cr else None,
                     human_decision=payload.decision)

    if payload.decision == "approve":
        # Human confirms the AI's NEEDS_REVIEW call stands as the final answer.
        review.final_result = None
        from datetime import datetime
        review.resolved_at = datetime.utcnow()

    elif payload.decision == "correct":
        # Human overrides the outcome directly.
        new_status = payload.corrected_status or "OK"
        if cr:
            cr.status = new_status
            cr.has_defect = new_status == "MISMATCH"
            cr.defect_fields = ComparisonResult.encode_fields(payload.corrected_defect_fields or [])
            cr.review_reason = None
            session.add(cr)
        email.status = new_status
        session.add(email)
        import json
        review.final_result = json.dumps({
            "status": new_status,
            "defect_fields": payload.corrected_defect_fields or [],
        })
        from datetime import datetime
        review.resolved_at = datetime.utcnow()

    elif payload.decision == "retry":
        # Re-run the pipeline for this email (e.g. after a corrected
        # attachment has been supplied out of band).
        email.status = "PENDING"
        session.add(email)
        session.add(review)
        session.commit()
        with session_scope() as bg_session:
            process_email_job(bg_session, email_id)
        return {"email_id": email_id, "decision": "retry", "status": "reprocessed"}

    session.add(review)
    session.commit()
    return {"email_id": email_id, "decision": payload.decision, "status": email.status}
