"""Cabeçalhos de segurança e limite de tamanho do corpo das requisições."""
from __future__ import annotations

import logging

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

# A tela usa apenas recursos inline do próprio domínio; nada de CDN.
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")

CABECALHOS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": CSP,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, producao: bool = False) -> None:
        super().__init__(app)
        self.producao = producao

    async def dispatch(self, request: Request, call_next) -> Response:
        resposta = await call_next(request)
        for chave, valor in CABECALHOS.items():
            resposta.headers.setdefault(chave, valor)
        if self.producao:
            resposta.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        # não anunciar o servidor; MutableHeaders não tem pop, então é del com checagem
        if "server" in resposta.headers:
            del resposta.headers["server"]
        return resposta


class LimiteTamanhoMiddleware(BaseHTTPMiddleware):
    """Recusa corpos grandes antes de lê-los: a API só recebe texto de chamado."""

    def __init__(self, app, max_kb: int = 256) -> None:
        super().__init__(app)
        self.max_bytes = max_kb * 1024

    async def dispatch(self, request: Request, call_next) -> Response:
        tamanho = request.headers.get("content-length")
        if tamanho and tamanho.isdigit() and int(tamanho) > self.max_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": f"Corpo acima do limite de {self.max_bytes // 1024} KB"},
            )
        return await call_next(request)
