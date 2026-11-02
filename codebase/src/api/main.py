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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.schemas_ui import (
    ConfigResponse,
    AdjudicationConfig,
    InvocationRequest,
    InvocationResponse,
)
from src.api.routes.claims import router as claims_router
from src.api.routes.review import router as review_router

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

app.include_router(claims_router, prefix="/claims", tags=["claims"])
app.include_router(review_router, prefix="/review", tags=["review"])


# ── Serve the compiled React console ─────────────────────────────────────────

_CONSOLE_HTML = Path(__file__).parents[2] / "data" / "ClaimSense_Console.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
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
                "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
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
    Validates RBAC, runs the deterministic signal layer, then calls the
    3-stage agent pipeline (or falls back to rule engine if LLM unavailable).
    """
    # Extract JWT adjuster identity from Authorization header (DEC-005)
    auth = request.headers.get("Authorization", "")
    jwt_token = auth.removeprefix("Bearer ").strip() or None

    try:
        from src.api.invocation_handler import handle_invocation
        return await handle_invocation(body, jwt_token)
    except Exception as exc:
        logger.exception("Invocation failed for %s", body.claim_id)
        raise HTTPException(status_code=500, detail=str(exc))
