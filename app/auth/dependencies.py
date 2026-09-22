"""Autenticação: aceita JWT (Authorization: Bearer) ou API key (X-API-Key)."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import decode_access_token
from app.config import Settings, get_settings
from app.users import repository
from app.users.models import User

# auto_error=False: sem isso o FastAPI recusaria antes de tentarmos a outra forma
bearer = HTTPBearer(auto_error=False, description="Token obtido em POST /auth/login")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                              description="API key do cliente (integrações servidor a servidor)")

NAO_AUTORIZADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciais ausentes ou inválidas. Use Authorization: Bearer <token> ou X-API-Key.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _ativo(user: User | None) -> User:
    if not user or not user.is_active:
        raise NAO_AUTORIZADO
    return user


def get_current_user(
    request: Request,
    credenciais: HTTPAuthorizationCredentials | None = Depends(bearer),
    chave: str | None = Depends(api_key_header),
    settings: Settings = Depends(get_settings),
) -> User:
    if credenciais and credenciais.credentials:
        try:
            username = decode_access_token(credenciais.credentials, settings)
        except ValueError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc),
                                headers={"WWW-Authenticate": "Bearer"}) from exc
        user = _ativo(repository.get_user_by_username(settings.db_path, username))
        request.state.auth = "jwt"
        return user

    if chave:
        user = _ativo(repository.get_user_by_api_key(settings.db_path, chave))
        request.state.auth = "api_key"
        return user

    raise NAO_AUTORIZADO


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso restrito a administradores")
    return current_user
