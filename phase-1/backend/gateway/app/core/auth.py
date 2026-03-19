"""JWT authentication and RBAC role hierarchy."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

ROLE_HIERARCHY = ["developer", "sre", "platform_engineer", "engineering_manager"]
security_scheme = HTTPBearer()


class CurrentUser(BaseModel):
    username: str
    role: str
    team: Optional[str] = None

    def has_role(self, required: str) -> bool:
        try:
            return ROLE_HIERARCHY.index(self.role) >= ROLE_HIERARCHY.index(required)
        except ValueError:
            return False


def create_access_token(username: str, role: str, team: Optional[str] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": username, "role": role, "team": team, "exp": expire, "type": "access"},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": username, "role": role, "exp": expire, "type": "refresh"},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security_scheme),
) -> CurrentUser:
    try:
        payload = jwt.decode(credentials.credentials, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return CurrentUser(username=payload["sub"], role=payload.get("role", "developer"), team=payload.get("team"))
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"error": "invalid_token", "message": str(exc)},
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def require_role(minimum_role: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not current_user.has_role(minimum_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"error": "forbidden",
                                        "message": f"Requires '{minimum_role}' role or higher."})
        return current_user
    return _check


async def require_internal_token(
    credentials: HTTPAuthorizationCredentials = Security(security_scheme),
) -> bool:
    if credentials.credentials != settings.NERVE_INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"error": "invalid_internal_token"})
    return True
