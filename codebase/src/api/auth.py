"""
Auth layer — mimics Cognito User Pool token issuance for local dev.

POST /auth/login   → validates credentials, returns JWT (sub + cognito:groups)
GET  /auth/me      → decodes the bearer token, returns caller identity

In production, replace _issue_token with a real Cognito InitiateAuth call and
replace verify_jwt with JWKS-based RS256 verification against:
  https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json
"""
import time
import logging
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET
from src.api.schemas_ui import LoginRequest, LoginResponse, CallerIdentity

logger = logging.getLogger(__name__)
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

# ── Local-dev user store (mirrors portal fixture users) ───────────────────────
# password is intentionally trivial — this store is never used in production.
_USERS: dict[str, dict] = {
    "alice": {"password": "alice123",  "group": "adjuster"},
    "bob":   {"password": "bob123",    "group": "adjuster"},
    "carol": {"password": "carol123",  "group": "senior-adjuster"},
    "david": {"password": "david123",  "group": "fraud-investigator"},
    "eve":   {"password": "eve123",    "group": "total-loss-appraiser"},
}


# ── Token helpers ─────────────────────────────────────────────────────────────

def _issue_token(user_id: str, group: str) -> str:
    """Sign a JWT that mirrors the Cognito access-token shape."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "cognito:groups": [group],
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
        "iss": "claimsense-local",          # replaced by Cognito issuer URL in prod
        "token_use": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> CallerIdentity:
    """
    Decode and validate a JWT.  Returns CallerIdentity on success.
    Raises HTTPException 401 on any failure.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")

    groups: list[str] = payload.get("cognito:groups", [])
    if not groups:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No group claim in token")

    return CallerIdentity(user_id=payload["sub"], group=groups[0])


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """
    Authenticate an adjuster and return a signed JWT.
    Cognito equivalent: InitiateAuth (USER_PASSWORD_AUTH flow).
    """
    user = _USERS.get(body.username)
    if not user or user["password"] != body.password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = _issue_token(body.username, user["group"])
    logger.info("Login: user=%s group=%s", body.username, user["group"])
    return LoginResponse(
        access_token=token,
        token_type="Bearer",
        expires_in=JWT_EXPIRY_SECONDS,
        user_id=body.username,
        group=user["group"],
    )


@router.get("/me", response_model=CallerIdentity)
async def me(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]):
    """Return the decoded identity from the bearer token."""
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    return verify_jwt(creds.credentials)
