"""Embeddings via OpenAI. Calculamos aqui (e não pelo Chroma) para controlar modelo, lote e erros."""
from __future__ import annotations

import logging
from functools import lru_cache

from openai import OpenAI

from app.config import get_settings

log = logging.getLogger(__name__)

BATCH = 100


@lru_cache
def get_openai() -> OpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada (veja .env.example)")
    return OpenAI(api_key=s.openai_api_key)


def embed_textos(textos: list[str]) -> list[list[float]]:
    if not textos:
        return []
    client = get_openai()
    model = get_settings().openai_embedding_model
    out: list[list[float]] = []
    for i in range(0, len(textos), BATCH):
        lote = [t.replace("\n", " ") if t else " " for t in textos[i : i + BATCH]]
        resp = client.embeddings.create(model=model, input=lote)
        # garante a ordem original
        out.extend([d.embedding for d in sorted(resp.data, key=lambda d: d.index)])
        log.info("embeddings %d/%d", min(i + BATCH, len(textos)), len(textos))
    return out


def embed_texto(texto: str) -> list[float]:
    return embed_textos([texto])[0]
