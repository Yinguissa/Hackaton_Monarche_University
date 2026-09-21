from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.auth import require_auth
from app.database.connection import get_session
from app.models.document import Document
from app.models.shipment import ShipmentField

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_auth)])


@router.get("/{email_id}")
def get_documents(email_id: str, session: Session = Depends(get_session)):
    docs = session.exec(select(Document).where(Document.email_id == email_id)).all()
    if not docs:
        raise HTTPException(status_code=404, detail="No documents for this email")
    out = []
    for d in docs:
        sf = session.exec(select(ShipmentField).where(ShipmentField.document_id == d.id)).first()
        out.append({
            "filename": d.filename,
            "document_type": d.document_type,
            "readable": d.readable,
            "used_ocr": d.used_ocr,
            "extracted_text": d.extracted_text,
            "fields": sf.model_dump(exclude={"id", "document_id"}) if sf else None,
        })
    return out
