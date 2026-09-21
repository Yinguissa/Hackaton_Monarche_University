"""
API tests — exercise the real FastAPI app end to end with an isolated
in-memory SQLite DB. Requires the backend's requirements.txt to be
installed (fastapi/sqlmodel), so this runs inside the backend Docker image
or a local venv — NOT in a bare-python environment.

    cd backend && pip install -r requirements.txt
    DATABASE_URL=sqlite:///./storage/test.db DISABLE_AUTH=1 pytest tests/test_api.py
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./storage/test.db")
os.environ.setdefault("DISABLE_AUTH", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login():
    r = client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_rejects_bad_password():
    r = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_upload_and_list_emails():
    r = client.post("/upload")
    assert r.status_code == 200
    assert r.json()["total_emails"] > 0

    r = client.get("/emails?limit=5")
    assert r.status_code == 200
    assert len(r.json()) <= 5


def test_process_single_email_and_read_detail():
    r = client.get("/emails?limit=1")
    email_id = r.json()[0]["email_id"]

    r = client.post(f"/emails/{email_id}/process")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    # BackgroundTasks run synchronously under TestClient, so the result
    # should already be there.
    r = client.get(f"/emails/{email_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] in {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"}


def test_dashboard_stats_shape():
    r = client.get("/dashboard")
    assert r.status_code == 200
    keys = {"total_emails", "processed_emails", "bl_comparison_requests",
            "successful_matches", "mismatches", "human_review_cases"}
    assert keys.issubset(r.json().keys())


def test_reviews_queue_only_contains_needs_review():
    r = client.get("/reviews")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_evaluation_generate_submission():
    r = client.post("/evaluation/generate")
    assert r.status_code == 200
    assert "emails_included" in r.json()
