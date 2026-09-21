"""
main.py — FastAPI application entrypoint.

Modular monolith: routers per domain (emails/documents/comparison/reviews/
evaluation) all import from the same services/ layer, no microservices.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.connection import init_db
from app.api import auth, emails, documents, comparison, reviews, evaluation

app = FastAPI(
    title="AI Shipping Document Verification Platform",
    description="Averis x Monash Hackathon 2026 — SI vs BL verification API",
    version="1.0.0",
)

# Ensure the schema exists as soon as the app is imported, not only when the
# FastAPI startup hook fires. Some tests and lightweight boots do not invoke
# startup handlers before the first request is hit.
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon demo; tighten before any real deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(emails.router)
app.include_router(documents.router)
app.include_router(comparison.router)
app.include_router(reviews.router)
app.include_router(evaluation.router)
