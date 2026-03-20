from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials

from apps.workers.common.settings import Settings, get_settings


basic_security = HTTPBasic()
bearer_security = HTTPBearer(auto_error=False)


def require_validation_user(
    credentials: HTTPBasicCredentials = Depends(basic_security),
    settings: Settings = Depends(get_settings),
) -> str:
    if not (
        secrets.compare_digest(credentials.username, settings.validation_username)
        and secrets.compare_digest(credentials.password, settings.validation_password)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid validation credentials")
    return credentials.username


def require_internal_token(
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_security),
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    candidate = x_internal_token or (bearer.credentials if bearer else None)
    if not candidate or not secrets.compare_digest(candidate, settings.internal_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")
