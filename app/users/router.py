"""Gestão de clientes da API. Tudo aqui exige um usuário administrador."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_admin_user
from app.auth.router import ApiKeyResponse
from app.config import Settings, get_settings
from app.users import repository
from app.users.models import User
from app.users.schemas import (
    AtualizarUsuarioRequest,
    CriarUsuarioRequest,
    UsoResponse,
    UsuarioResumo,
)

router = APIRouter(prefix="/usuarios", tags=["Usuários (admin)"],
                   dependencies=[Depends(get_admin_user)])


def _resumo(u: User) -> UsuarioResumo:
    return UsuarioResumo(username=u.username, rate_limit_per_hour=u.rate_limit_per_hour,
                         is_admin=u.is_admin, is_active=u.is_active,
                         api_key_prefix=u.api_key_prefix, criado_em=u.criado_em)


@router.get("", response_model=list[UsuarioResumo])
def listar(settings: Settings = Depends(get_settings)) -> list[UsuarioResumo]:
    return [_resumo(u) for u in repository.list_users(settings.db_path)]


@router.post("", response_model=UsuarioResumo, status_code=status.HTTP_201_CREATED)
def criar(body: CriarUsuarioRequest,
          settings: Settings = Depends(get_settings)) -> UsuarioResumo:
    try:
        u = repository.create_user(
            settings.db_path, body.username, body.password,
            body.rate_limit_per_hour or settings.default_rate_limit_per_hour, body.is_admin)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _resumo(u)


@router.patch("/{username}", response_model=UsuarioResumo)
def atualizar(username: str, body: AtualizarUsuarioRequest,
              settings: Settings = Depends(get_settings)) -> UsuarioResumo:
    u = repository.update_user(settings.db_path, username, password=body.password,
                               rate_limit_per_hour=body.rate_limit_per_hour,
                               is_active=body.is_active)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    return _resumo(u)


@router.post("/{username}/api-key", response_model=ApiKeyResponse,
             status_code=status.HTTP_201_CREATED)
def gerar_api_key(username: str, settings: Settings = Depends(get_settings)) -> ApiKeyResponse:
    """Gera a API key de um cliente (ex.: a do processo Lecom). A anterior deixa de valer."""
    chave = repository.rotacionar_api_key(settings.db_path, username)
    if not chave:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    return ApiKeyResponse(api_key=chave, prefixo=chave[:12])


@router.delete("/{username}/api-key", status_code=status.HTTP_204_NO_CONTENT)
def revogar_api_key(username: str, settings: Settings = Depends(get_settings)) -> None:
    if not repository.revogar_api_key(settings.db_path, username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")


@router.get("/uso/relatorio", response_model=UsoResponse)
def uso(username: str | None = Query(None), inicio: str | None = Query(None),
        fim: str | None = Query(None),
        settings: Settings = Depends(get_settings)) -> UsoResponse:
    return UsoResponse(itens=repository.get_usage(settings.db_path, username, inicio, fim))
