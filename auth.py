"""
OpenLabel — Authentication
JWT-based auth using Supabase as the identity provider.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from database import get_supabase_public


# Bearer token extractor — auto_error=False allows optional auth
security_required = HTTPBearer(auto_error=True)
security_optional = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_required),
) -> dict:
    """
    REQUIRED auth dependency.
    Validates the JWT token via Supabase and returns the user object.
    Raises 401 if token is invalid or missing.
    """
    try:
        supabase = get_supabase_public()
        user_response = supabase.auth.get_user(credentials.credentials)
        return user_response.user
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> Optional[dict]:
    """
    OPTIONAL auth dependency.
    Returns user if valid token provided, None otherwise.
    Used by scan endpoints to allow anonymous scans.
    """
    if credentials is None:
        return None
    try:
        supabase = get_supabase_public()
        user_response = supabase.auth.get_user(credentials.credentials)
        return user_response.user
    except Exception:
        return None
