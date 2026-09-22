"""Limite de requisições por cliente e bloqueio de força bruta no login."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import get_current_user
from app.config import Settings, get_settings
from app.users import repository
from app.users.models import User


class RateLimiter:
    """Janela deslizante em memória. Basta para uma instância; com várias, migrar para Redis."""

    def __init__(self) -> None:
        self._req: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, chave: str, limite: int, janela_s: int = 3600) -> tuple[bool, int]:
        agora = time.time()
        with self._lock:
            fila = self._req[chave]
            while fila and fila[0] <= agora - janela_s:
                fila.popleft()
            if len(fila) >= limite:
                return False, 0
            fila.append(agora)
            return True, limite - len(fila)

    def limpar(self) -> None:
        with self._lock:
            self._req.clear()


rate_limiter = RateLimiter()


class LoginAttemptLimiter:
    """Bloqueia tentativas de login por IP após falhas repetidas."""

    def __init__(self, max_falhas: int = 10, janela_s: int = 900) -> None:
        self._falhas: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self.max_falhas = max_falhas
        self.janela_s = janela_s

    def _podar(self, chave: str, agora: float) -> deque[float]:
        fila = self._falhas[chave]
        while fila and fila[0] <= agora - self.janela_s:
            fila.popleft()
        return fila

    def is_blocked(self, chave: str) -> bool:
        with self._lock:
            return len(self._podar(chave, time.time())) >= self.max_falhas

    def register_failure(self, chave: str) -> None:
        with self._lock:
            agora = time.time()
            self._podar(chave, agora).append(agora)

    def reset(self, chave: str) -> None:
        with self._lock:
            self._falhas.pop(chave, None)


login_limiter = LoginAttemptLimiter()


def client_ip(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def enforce_rate_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> User:
    if not current_user.is_admin:
        permitido, restantes = rate_limiter.check(
            current_user.username, current_user.rate_limit_per_hour)
        if not permitido:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(f"Limite de {current_user.rate_limit_per_hour} requisições por hora "
                        "atingido. Tente novamente mais tarde."),
                headers={"Retry-After": "3600", "X-RateLimit-Remaining": "0"},
            )
        request.state.rate_remaining = restantes

    repository.increment_usage(settings.db_path, current_user.username, request.url.path)
    return current_user
