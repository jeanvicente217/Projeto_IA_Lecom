"""Login, dados do usuário logado e gestão da própria API key."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.service import create_access_token
from app.config import Settings, get_settings
from app.rate_limit import client_ip, login_limiter
from app.users import repository
from app.users.models import User

router = APIRouter(prefix="/auth", tags=["Autenticação"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_em_minutos: int


class UsuarioResponse(BaseModel):
    username: str
    rate_limit_per_hour: int
    is_admin: bool
    api_key_prefix: str | None = Field(None, description="Início da API key ativa, se houver")


class ApiKeyResponse(BaseModel):
    api_key: str = Field(..., description="Guarde agora: a chave não é exibida outra vez")
    prefixo: str


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request,
          settings: Settings = Depends(get_settings)) -> TokenResponse:
    ip = client_ip(request)
    if login_limiter.is_blocked(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Muitas tentativas de login. Tente novamente em 15 minutos.",
                            headers={"Retry-After": "900"})

    user = repository.authenticate_user(settings.db_path, body.username, body.password)
    if not user:
        login_limiter.register_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos",
                            headers={"WWW-Authenticate": "Bearer"})

    login_limiter.reset(ip)
    return TokenResponse(access_token=create_access_token(user.username, settings),
                         expira_em_minutos=settings.jwt_expire_minutes)


@router.get("/me", response_model=UsuarioResponse)
def me(current_user: User = Depends(get_current_user)) -> UsuarioResponse:
    return UsuarioResponse(username=current_user.username,
                           rate_limit_per_hour=current_user.rate_limit_per_hour,
                           is_admin=current_user.is_admin,
                           api_key_prefix=current_user.api_key_prefix)


@router.post("/api-key", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
def gerar_api_key(current_user: User = Depends(get_current_user),
                  settings: Settings = Depends(get_settings)) -> ApiKeyResponse:
    """Gera uma API key para o usuário logado. A chave anterior deixa de valer."""
    chave = repository.rotacionar_api_key(settings.db_path, current_user.username)
    if not chave:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    return ApiKeyResponse(api_key=chave, prefixo=chave[:12])


@router.delete("/api-key", status_code=status.HTTP_204_NO_CONTENT)
def revogar_api_key(current_user: User = Depends(get_current_user),
                    settings: Settings = Depends(get_settings)) -> None:
    repository.revogar_api_key(settings.db_path, current_user.username)
