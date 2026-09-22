from __future__ import annotations

from pydantic import BaseModel, Field


class UsuarioResumo(BaseModel):
    username: str
    rate_limit_per_hour: int
    is_admin: bool
    is_active: bool
    api_key_prefix: str | None = None
    criado_em: str | None = None


class CriarUsuarioRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(..., min_length=12, description="mínimo de 12 caracteres")
    rate_limit_per_hour: int | None = Field(None, ge=1, le=100_000)
    is_admin: bool = False


class AtualizarUsuarioRequest(BaseModel):
    password: str | None = Field(None, min_length=12)
    rate_limit_per_hour: int | None = Field(None, ge=1, le=100_000)
    is_active: bool | None = None


class UsoResponse(BaseModel):
    itens: list[dict]
