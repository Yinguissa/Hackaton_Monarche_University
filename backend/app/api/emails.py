from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.api.auth import require_auth
from app.database.connection import get_session, session_scope
from app.models.email import Email
from app.models.document import Document
from app.models.shipment import ShipmentField
from app.models.discrepancy import ComparisonResult
from app.services.processing import ingest_dataset, process_email_job

router = APIRouter(tags=["emails"], dependencies=[Depends(require_auth)])


def _run_process_job(email_id: str):
    """Runs in a BackgroundTasks worker thread — needs its own DB session."""
    with session_scope() as session:
        process_email_job(session, email_id)


@router.post("/upload")
def upload_dataset(session: Session = Depends(get_session)):
    """Load storage/inbox/*.json into the emails table. In this hackathon
    build the dataset already ships inside the container's storage/ folder,
    so this simply (idempotently) syncs the DB with what's on disk; in a
    real deployment this endpoint would accept a multipart file upload of a
    fresh inbox export instead."""
    created = ingest_dataset(session)
    total = session.exec(select(Email)).all()
    return {"created": created, "total_emails": len(total)}


@router.get("/emails")
def list_emails(
    category: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    query = select(Email)
    if category:
        query = query.where(Email.category == category)
    if status:
        query = query.where(Email.status == status)
    rows = session.exec(query.offset(offset).limit(limit)).all()
    return [
        {
            "email_id": r.email_id,
            "from": r.sender,
            "subject": r.subject,
            "category": r.category,
            "status": r.status,
            "classification_confidence": r.classification_confidence,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/emails/full")
def list_emails_full(session: Session = Depends(get_session)):
    """Bulk endpoint used by the frontend to hydrate its whole UI state in a
    single round trip: every email plus its documents/fields/comparison
    result already joined. Fine at this dataset's scale (hundreds of rows);
    paginate this before pointing it at a much larger inbox."""
    rows = session.exec(select(Email)).all()
    out = []
    for e in rows:
        docs = session.exec(select(Document).where(Document.email_id == e.email_id)).all()
        doc_payload = []
        for d in docs:
            sf = session.exec(
                select(ShipmentField).where(ShipmentField.document_id == d.id)
            ).first()
            doc_payload.append({
                "filename": d.filename,
                "document_type": d.document_type,
                "readable": d.readable,
                "used_ocr": d.used_ocr,
                "fields": sf.model_dump(exclude={"id", "document_id"}) if sf else None,
            })
        cr = session.exec(
            select(ComparisonResult).where(ComparisonResult.email_id == e.email_id)
        ).first()
        out.append({
            "email_id": e.email_id,
            "from": e.sender,
            "subject": e.subject,
            "body": e.body,
            "category": e.category,
            "classification_confidence": e.classification_confidence,
            "status": e.status,
            "created_at": e.created_at,
            "documents": doc_payload,
            "comparison": {
                "status": cr.status,
                "has_defect": cr.has_defect,
                "defect_fields": cr.defect_fields_list(),
                "review_reason": cr.review_reason,
            } if cr else None,
        })
    return out


@router.get("/emails/{email_id}")
def get_email(email_id: str, session: Session = Depends(get_session)):
    email = session.exec(select(Email).where(Email.email_id == email_id)).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    docs = session.exec(select(Document).where(Document.email_id == email_id)).all()
    doc_payload = []
    for d in docs:
        sf = session.exec(
            select(ShipmentField).where(ShipmentField.document_id == d.id)
        ).first()
        doc_payload.append({
            "filename": d.filename,
            "document_type": d.document_type,
            "storage_path": d.storage_path,
            "readable": d.readable,
            "used_ocr": d.used_ocr,
            "extracted_text": d.extracted_text,
            "fields": sf.model_dump(exclude={"id", "document_id"}) if sf else None,
        })

    comparison = session.exec(
        select(ComparisonResult).where(ComparisonResult.email_id == email_id)
    ).first()

    return {
        "email_id": email.email_id,
        "from": email.sender,
        "subject": email.subject,
        "body": email.body,
        "category": email.category,
        "classification_confidence": email.classification_confidence,
        "status": email.status,
        "documents": doc_payload,
        "comparison": {
            "status": comparison.status,
            "has_defect": comparison.has_defect,
            "defect_fields": comparison.defect_fields_list(),
            "review_reason": comparison.review_reason,
        } if comparison else None,
    }


@router.post("/emails/{email_id}/process")
def process_email_endpoint(
    email_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    email = session.exec(select(Email).where(Email.email_id == email_id)).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    background_tasks.add_task(_run_process_job, email_id)
    return {"email_id": email_id, "status": "queued"}


@router.post("/emails/process-all")
def process_all_emails(background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """Convenience endpoint used by the Evaluation dashboard's
    'Generate Submission' button: queues every unprocessed email."""
    pending = session.exec(select(Email).where(Email.status == "PENDING")).all()
    for e in pending:
        background_tasks.add_task(_run_process_job, e.email_id)
    return {"queued": len(pending)}


@router.get("/dashboard")
def dashboard_stats(session: Session = Depends(get_session)):
    emails = session.exec(select(Email)).all()
    total = len(emails)
    processed = len([e for e in emails if e.status != "PENDING"])
    bl_requests = len([e for e in emails if e.category == "BL_COMPARISON"])
    matches = len([e for e in emails if e.status == "OK"])
    mismatches = len([e for e in emails if e.status == "MISMATCH"])
    reviews = len([e for e in emails if e.status == "NEEDS_REVIEW"])
    by_category = {}
    for e in emails:
        if e.category:
            by_category[e.category] = by_category.get(e.category, 0) + 1
    return {
        "total_emails": total,
        "processed_emails": processed,
        "bl_comparison_requests": bl_requests,
        "successful_matches": matches,
        "mismatches": mismatches,
        "human_review_cases": reviews,
        "by_category": by_category,
    }
