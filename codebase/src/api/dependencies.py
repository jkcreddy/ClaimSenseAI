"""
FastAPI dependencies — reusable Depends() callables.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.auth import verify_jwt
from src.api.schemas_ui import CallerIdentity

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CallerIdentity:
    """
    Extracts and validates the JWT from the Authorization: Bearer header.
    Inject with:  user: Annotated[CallerIdentity, Depends(get_current_user)]
    """
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    return verify_jwt(creds.credentials)
