"""Cliente mínimo da Evolution API: envio de texto e leitura do webhook de mensagem.

Compatível com o formato de webhook da Evolution v2 (evento `messages.upsert`). O payload
traz `apikey` da instância, que conferimos como segunda camada além do token da URL.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


class EvolutionClient:
    def __init__(self, settings: Settings) -> None:
        self.base = settings.evolution_url.rstrip("/")
        self.instance = settings.evolution_instance
        self.api_key = settings.evolution_api_key

    def configurado(self) -> bool:
        return bool(self.base and self.instance and self.api_key)

    def enviar_texto(self, telefone: str, texto: str) -> bool:
        """POST /message/sendText/{instance}. Devolve True se a Evolution aceitou."""
        if not self.configurado():
            log.error("Evolution não configurada (EVOLUTION_URL/INSTANCE/API_KEY)")
            return False
        url = f"{self.base}/message/sendText/{self.instance}"
        corpo = {"number": telefone, "text": texto}
        try:
            r = httpx.post(url, json=corpo, headers={"apikey": self.api_key}, timeout=20)
            if r.status_code >= 400:
                log.error("Evolution recusou envio (%s): %s", r.status_code, r.text[:200])
                return False
            return True
        except httpx.HTTPError as e:
            log.error("falha ao chamar a Evolution: %s", type(e).__name__)
            return False


def extrair_mensagem(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Do webhook, devolve {telefone_jid, id, texto, from_me, grupo} ou None se não for texto.

    Aceita tanto `data` como objeto quanto como lista (a Evolution já usou os dois).
    """
    evento = str(payload.get("event") or "").replace("_", ".").lower()
    if evento and evento != "messages.upsert":
        return None
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None

    key = data.get("key") or {}
    jid = key.get("remoteJid") or ""
    # em contas com LID o número real vem num campo alternativo
    if jid.endswith("@lid"):
        jid = key.get("senderPn") or key.get("remoteJidAlt") or data.get("senderPn") or jid

    msg = data.get("message") or {}
    texto = (msg.get("conversation")
             or (msg.get("extendedTextMessage") or {}).get("text")
             or (msg.get("ephemeralMessage") or {}).get("message", {}).get("conversation")
             or "")
    return {
        "jid": jid,
        "id": key.get("id") or data.get("messageId") or "",
        "texto": (texto or "").strip(),
        "from_me": bool(key.get("fromMe")),
        "grupo": jid.endswith("@g.us"),
        "tipo": data.get("messageType") or "",
    }
