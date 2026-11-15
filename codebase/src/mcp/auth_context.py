"""
Auth context — carries the caller's JWT through the async call chain.

Uses contextvars (not threading.local) so it is safe with asyncio and
works correctly when multiple concurrent requests are in flight.

Usage:
    # In the request handler, before calling any MCP tool:
    token = auth_ctx.set("eyJ...")
    try:
        ...
    finally:
        auth_ctx.reset(token)

    # Inside any MCP tool or downstream coroutine:
    jwt = get_jwt()          # returns the token or raises if not set
"""
from contextvars import ContextVar
from typing import Optional, Any

# Holds the raw JWT string for the current async context
_jwt_var: ContextVar[Optional[str]] = ContextVar("adjuster_jwt", default=None)


def set_jwt(token: str):
    """Bind the JWT to the current async context. Returns the Token for reset."""
    return _jwt_var.set(token)


def get_jwt() -> str:
    """Return the JWT for the current context. Raises RuntimeError if not set."""
    token = _jwt_var.get()
    if token is None:
        raise RuntimeError("No JWT in context — call set_jwt() before invoking MCP tools.")
    return token


def get_jwt_optional() -> Optional[str]:
    """Return the JWT or None if not set (use in non-auth paths)."""
    return _jwt_var.get()


def reset_jwt(ctx_token) -> None:
    """Restore the previous context value (call in finally block)."""
    _jwt_var.reset(ctx_token)


def set_current_adjuster(identity: Any):
    """Convenience helper to set context identity from AdjusterIdentity (used in incident drills)."""
    from src.api.auth import _issue_token
    user_id = getattr(identity, "adjuster_id", "alice")
    role = getattr(identity, "role", "adjuster")
    group = role.lower().replace("_", "-")
    token = _issue_token(user_id, group)
    return set_jwt(token)
