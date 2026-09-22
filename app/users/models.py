from dataclasses import dataclass


@dataclass
class User:
    """Cliente da API. Autentica por senha (JWT) ou por API key.

    `api_key_hash` guarda o SHA-256 da chave; a chave em claro só existe no momento em que
    é gerada e nunca é persistida.
    """

    id: int
    username: str
    password_hash: str
    api_key_hash: str | None
    api_key_prefix: str | None
    rate_limit_per_hour: int
    is_admin: bool
    is_active: bool
    criado_em: str | None = None
