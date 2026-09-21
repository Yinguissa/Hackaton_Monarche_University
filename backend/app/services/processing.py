"""
processing.py — bridges the pure pipeline (services/pipeline.py) to the
database. This is what the FastAPI BackgroundTasks worker calls per email.
"""
import json
import os

from sqlmodel import Session, select

from app.models.email import Email
from app.models.document import Document
from app.models.shipment import ShipmentField
from app.models.discrepancy import ComparisonResult
from app.services import normalizer as norm
from app.services.pipeline import process_email

STORAGE_ROOT = os.environ.get(
    "STORAGE_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "storage")
)


def process_email_job(session: Session, email_id: str):
    """Load an Email row + its inbox JSON, run the pipeline, and persist
    Document / ShipmentField / ComparisonResult rows. Safe to re-run (clears
    any prior documents/comparison for this email first)."""
    email_row = session.exec(select(Email).where(Email.email_id == email_id)).first()
    if email_row is None:
        raise ValueError(f"Unknown email_id: {email_id}")

    email_row.status = "PROCESSING"
    session.add(email_row)
    session.commit()

    # The raw dataset JSON is the source of truth for subject/body/attachments;
    # the DB row mirrors it for querying.
    inbox_path = os.path.join(STORAGE_ROOT, "inbox", f"{email_id}.json")
    with open(inbox_path) as f:
        email_json = json.load(f)

    result = process_email(email_json, STORAGE_ROOT)

    # --- persist documents + shipment fields -----------------------------
    # clear any previous run's rows for this email (idempotent re-process)
    for doc in session.exec(select(Document).where(Document.email_id == email_id)):
        session.delete(doc)
    for cr in session.exec(select(ComparisonResult).where(ComparisonResult.email_id == email_id)):
        session.delete(cr)
    session.commit()

    for att in result.get("attachments_meta", []):
        extraction = att["extraction"]
        doc = Document(
            email_id=email_id,
            filename=os.path.basename(att["path"]),
            document_type=extraction["doc_type_guess"],
            storage_path=att["path"],
            extracted_text=extraction["raw_text"][:20000],  # cap for DB sanity
            readable=extraction["readable"],
            used_ocr=extraction["used_ocr"],
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)

        fields = extraction["fields"]
        session.add(ShipmentField(
            document_id=doc.id,
            shipper=fields.get("shipper"),
            consignee=fields.get("consignee"),
            notify_party=fields.get("notify_party"),
            port_loading=fields.get("port_of_loading"),
            port_discharge=fields.get("port_of_discharge"),
            container_count=norm.normalize_int_value(fields.get("container_count")),
            gross_weight=norm.normalize_int_value(fields.get("gross_weight_kg")),
        ))

    # --- persist comparison result ---------------------------------------
    session.add(ComparisonResult(
        email_id=email_id,
        status=result["status"],
        has_defect=result["has_defect"],
        defect_fields=ComparisonResult.encode_fields(result["defect_fields"]),
        review_reason=result["review_reason"],
    ))

    # --- update the email row ---------------------------------------------
    email_row.category = result["category"]
    email_row.classification_confidence = result["classification_confidence"]
    email_row.status = result["status"]
    session.add(email_row)
    session.commit()

    return result


def ingest_dataset(session: Session, storage_root: str = None):
    """Load every storage/inbox/*.json file into the emails table (skips
    ones already present). Attachments stay on disk under storage/attachments
    and are only read at process-time."""
    storage_root = storage_root or STORAGE_ROOT
    inbox_dir = os.path.join(storage_root, "inbox")
    created = 0
    for fname in sorted(os.listdir(inbox_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(inbox_dir, fname)) as f:
            data = json.load(f)
        existing = session.exec(
            select(Email).where(Email.email_id == data["email_id"])
        ).first()
        if existing:
            continue
        session.add(Email(
            email_id=data["email_id"],
            sender=data.get("from", ""),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            status="PENDING",
        ))
        created += 1
    session.commit()
    return created
