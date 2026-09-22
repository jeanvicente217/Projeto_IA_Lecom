"""Senhas (bcrypt), tokens JWT e API keys."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import Settings, get_settings

ALGORITHM = "HS256"
PREFIXO_API_KEY = "nxp_"
# bcrypt ignora o que passa de 72 bytes; truncar evita senha longa virar senha curta silenciosamente
MAX_SENHA_BYTES = 72


def _senha_bytes(senha: str) -> bytes:
    return senha.encode("utf-8")[:MAX_SENHA_BYTES]


def verify_password(senha: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_senha_bytes(senha), password_hash.encode())
    except ValueError:
        return False


def get_password_hash(senha: str) -> str:
    return bcrypt.hashpw(_senha_bytes(senha), bcrypt.gensalt()).decode()


def create_access_token(subject: str, settings: Settings | None = None,
                        expires_delta: timedelta | None = None) -> str:
    settings = settings or get_settings()
    expira = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    return jwt.encode({"sub": subject, "exp": expira, "typ": "access"},
                      settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Token inválido ou expirado") from exc
    username = payload.get("sub")
    if not username:
        raise ValueError("Token sem identificação de usuário")
    return username


def gerar_api_key() -> tuple[str, str, str]:
    """Devolve (chave em claro, hash para guardar, prefixo para exibir).

    A chave em claro só existe aqui: o banco guarda apenas o hash.
    """
    chave = PREFIXO_API_KEY + secrets.token_urlsafe(32)
    return chave, hash_api_key(chave), chave[:12]


def hash_api_key(chave: str) -> str:
    """SHA-256 é suficiente e rápido: a chave tem entropia alta, diferente de uma senha."""
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()
