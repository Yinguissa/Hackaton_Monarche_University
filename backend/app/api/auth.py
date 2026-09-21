"""
auth.py — deliberately simple username/password authentication.

Per the spec: "Implement simple username/password authentication. Do not
build enterprise authentication." A single demo account is configurable via
env vars, and successful login returns an opaque bearer token held in an
in-memory set (fine for a hackathon demo / single backend process; swap for
JWT + a real user table if this ever needs to survive a restart or run with
multiple backend workers).
"""
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

DEMO_USERNAME = os.environ.get("APP_USERNAME", "admin")
DEMO_PASSWORD = os.environ.get("APP_PASSWORD", "admin123")

_valid_tokens = set()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    if payload.username != DEMO_USERNAME or payload.password != DEMO_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = secrets.token_hex(16)
    _valid_tokens.add(token)
    return LoginResponse(token=token, username=payload.username)


@router.post("/logout")
def logout(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    _valid_tokens.discard(token)
    return {"ok": True}


def require_auth(authorization: str = Header(default="")):
    """FastAPI dependency to protect a route. Disabled entirely when
    DISABLE_AUTH=1 (handy for local API testing / the hackathon demo)."""
    if os.environ.get("DISABLE_AUTH") == "1":
        return True
    token = authorization.replace("Bearer ", "").strip()
    if token not in _valid_tokens:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return True
