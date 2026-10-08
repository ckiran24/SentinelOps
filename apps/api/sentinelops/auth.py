import secrets
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .db import utcnow

bearer = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    username: str
    role: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


def login(request: LoginRequest) -> dict:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(403, "Demo login disabled. Use your configured identity provider.")
    if request.username not in ("operator", "approver") or not secrets.compare_digest(
        request.password, "sentinel-demo"
    ):
        raise HTTPException(401, "Invalid credentials")
    if settings.jwt_algorithm != "HS256":
        raise HTTPException(503, "Demo login requires configured HS256 signing")
    try:
        key = settings.verification_key()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    now = utcnow()
    claims = {
        "sub": request.username,
        "role": request.username,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(claims, key, algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": request.username,
        "username": request.username,
    }


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(401, "Bearer token required")
    settings = get_settings()
    try:
        key = settings.verification_key()
    except ValueError as exc:
        raise HTTPException(503, "Authentication signing configuration invalid") from exc
    try:
        payload = jwt.decode(
            credentials.credentials,
            key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "nbf", "sub", "role", "iss", "aud"]},
        )
        if (
            payload["role"] not in ("viewer", "operator", "approver")
            or not isinstance(payload["sub"], str)
            or not payload["sub"]
        ):
            raise ValueError("Invalid subject or role")
        return Principal(username=payload["sub"], role=payload["role"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, "Invalid or expired bearer token") from exc


def require_role(*roles):
    def dependency(user: Annotated[Principal, Depends(current_user)]):
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user

    return dependency
