"""
evaluation.py — evaluation integration.

Workflow: process dataset -> generate submission.json -> submit evaluation
-> receive score -> display score.

The scoring service is the organizers' own Docker package (sdoc-hackathon-
docker.zip); it is a separate service exposing `POST /submit`. We only need
its base URL (EVAL_SERVER_URL). If it isn't reachable (e.g. running the demo
without the organizers' scoring container available), `/evaluation/submit`
still returns the generated submission.json plus a clear error rather than
crashing, so the rest of the demo keeps working.
"""
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.auth import require_auth
from app.database.connection import get_session
from app.models.email import Email
from app.models.discrepancy import ComparisonResult

router = APIRouter(prefix="/evaluation", tags=["evaluation"], dependencies=[Depends(require_auth)])

EVAL_SERVER_URL = os.environ.get("EVAL_SERVER_URL", "http://eval-server:8000")
SUBMISSION_PATH = os.environ.get(
    "SUBMISSION_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "storage", "reports", "submission.json"),
)


def _build_submission(session: Session):
    emails = session.exec(select(Email)).all()
    submission = {}
    for e in emails:
        if e.status == "PENDING":
            continue  # not processed yet — leave out rather than guess
        cr = session.exec(
            select(ComparisonResult).where(ComparisonResult.email_id == e.email_id)
        ).first()
        submission[e.email_id] = {
            "category": e.category,
            "status": e.status,
            "review_reason": cr.review_reason if cr else None,
            "has_defect": cr.has_defect if cr else False,
            "defect_fields": cr.defect_fields_list() if cr else [],
        }
    return submission


@router.post("/generate")
def generate_submission(session: Session = Depends(get_session)):
    """Build submission.json from whatever has been processed so far and
    write it to storage/reports/submission.json."""
    submission = _build_submission(session)
    os.makedirs(os.path.dirname(SUBMISSION_PATH), exist_ok=True)
    with open(SUBMISSION_PATH, "w") as f:
        json.dump(submission, f, indent=2)
    return {"emails_included": len(submission), "path": SUBMISSION_PATH}


@router.post("/submit")
def submit_evaluation(session: Session = Depends(get_session)):
    """Generate the submission (if not already on disk) and POST it to the
    organizers' scoring server, returning the scoreboard JSON."""
    submission = _build_submission(session)
    os.makedirs(os.path.dirname(SUBMISSION_PATH), exist_ok=True)
    with open(SUBMISSION_PATH, "w") as f:
        json.dump(submission, f, indent=2)

    try:
        import httpx
        resp = httpx.post(f"{EVAL_SERVER_URL}/submit", json=submission, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not reach evaluation server at {EVAL_SERVER_URL} ({exc}). "
                f"submission.json was written to {SUBMISSION_PATH} — "
                "set EVAL_SERVER_URL to the organizers' scoring endpoint and retry."
            ),
        )


@router.get("/submission")
def get_submission():
    if not os.path.exists(SUBMISSION_PATH):
        raise HTTPException(status_code=404, detail="No submission generated yet")
    with open(SUBMISSION_PATH) as f:
        return json.load(f)
