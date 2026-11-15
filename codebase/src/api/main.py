"""
ClaimSense AI — FastAPI application
Serves:
  GET  /           → ClaimSense Console HTML (the compiled React UI)
  GET  /ping       → health check
  GET  /config     → adjudicator config consumed by the UI on mount
  POST /invocations → main agent invocation endpoint
"""
import os
import time
import logging
from pathlib import Path

import src.core.config  # Loads .env configuration

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.schemas_ui import (
    ConfigResponse,
    AdjudicationConfig,
    InvocationRequest,
    InvocationResponse,
)
from src.api.routes.claims import router as claims_router
from src.api.routes.review import router as review_router
from src.api.auth import router as auth_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ClaimSense AI",
    description="P&C claims co-pilot — adjudication recommendation API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static assets if present
_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

app.include_router(auth_router,   prefix="/auth",    tags=["auth"])
app.include_router(claims_router, prefix="/claims",  tags=["claims"])
app.include_router(review_router, prefix="/review",  tags=["review"])


# ── HTML file paths ──────────────────────────────────────────────────────────

_DATA = Path(__file__).parents[2] / "data"
_PORTAL_HTML  = _DATA / "claimsense_portal.html"
_CONSOLE_HTML = _DATA / "ClaimSense_Console.html"


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/portal")


@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
async def serve_portal():
    if _PORTAL_HTML.exists():
        return HTMLResponse(content=_PORTAL_HTML.read_text())
    return HTMLResponse(
        content="<h2>ClaimSense Portal not found. Place claimsense_portal.html in codebase/data/.</h2>",
        status_code=404,
    )


@app.get("/console", response_class=HTMLResponse, include_in_schema=False)
async def serve_console():
    if _CONSOLE_HTML.exists():
        return HTMLResponse(content=_CONSOLE_HTML.read_text())
    return HTMLResponse(
        content="<h2>ClaimSense Console not found. Place ClaimSense_Console.html in codebase/data/.</h2>",
        status_code=404,
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "ClaimSense AI"}


# ── Config (UI calls this on mount to populate the adjudicator pill) ──────────

@app.get("/config", response_model=ConfigResponse)
async def get_config():
    enabled = os.getenv("BEDROCK_REASONING_MODEL_ID") is not None
    return ConfigResponse(
        adjudication=AdjudicationConfig(
            enabled=enabled,
            model_id=os.getenv(
                "BEDROCK_REASONING_MODEL_ID",
                "bedrock/us.anthropic.claude-sonnet-4-6",
            ),
            region=os.getenv("AWS_REGION", "us-east-1"),
            effort="standard",
            provider_label="AWS Bedrock · LiteLLM",
        )
    )


# ── Invocations (main agent call) ─────────────────────────────────────────────

@app.post("/invocations", response_model=InvocationResponse)
async def invoke_agent(request: Request, body: InvocationRequest):
    """
    Receives a claim assessment request from the Console UI.
    Validates JWT, enforces RBAC, runs the deterministic signal layer, then
    calls the MCP tool chain with the caller's JWT forwarded.
    """
    auth = request.headers.get("Authorization", "")
    jwt_token = auth.removeprefix("Bearer ").strip() or None

    # Verify the JWT early so we fail fast before any data access
    if jwt_token:
        from src.api.auth import verify_jwt
        try:
            identity = verify_jwt(jwt_token)
            # Override body identity with verified JWT claims (DEC-005)
            body.user_id = identity.user_id
            body.group   = identity.group
        except Exception:
            pass  # let invocation_handler handle missing/bad JWT gracefully

    try:
        from src.api.invocation_handler import handle_invocation
        return await handle_invocation(body, jwt_token)
    except Exception as exc:
        logger.exception("Invocation failed for %s", body.claim_id)
        raise HTTPException(status_code=500, detail=str(exc))
