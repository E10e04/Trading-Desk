"""Dépendances communes : runtime, authentification par jeton."""
from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request

from ..config import get_settings
from ..runtime import Runtime


def runtime(request: Request) -> Runtime:
    return request.app.state.rt


def check_token(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, get_settings().api_token)


async def require_auth(request: Request, authorization: str | None = Header(default=None), x_api_token: str | None = Header(default=None)) -> None:
    tok = x_api_token
    if not tok and authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    if not check_token(tok):
        raise HTTPException(status_code=401, detail="jeton d'API manquant ou invalide")


Auth = Depends(require_auth)
