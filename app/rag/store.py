"""Acesso ao ChromaDB persistido em disco."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

from app.config import get_settings

META_FILE = "ingest_meta.json"


@lru_cache
def get_client() -> chromadb.ClientAPI:
    s = get_settings()
    s.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(s.chroma_dir))


def get_collection() -> Collection:
    s = get_settings()
    return get_client().get_or_create_collection(
        name=s.chroma_collection,
        metadata={"hnsw:space": "cosine", "embedding_model": s.openai_embedding_model},
    )


def recriar_collection() -> Collection:
    """Apaga a coleção e cria outra vazia. Usado para reindexar do zero."""
    s = get_settings()
    try:
        get_client().delete_collection(s.chroma_collection)
    except Exception:  # noqa: BLE001  (não existia ainda)
        pass
    return get_collection()


def _meta_path() -> Path:
    return get_settings().chroma_dir / META_FILE


def salvar_meta_ingestao(info: dict[str, Any]) -> None:
    info = {**info, "quando": datetime.now().isoformat(timespec="seconds")}
    _meta_path().write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def ler_meta_ingestao() -> dict[str, Any] | None:
    p = _meta_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def valores_distintos(
    campos: tuple[str, ...] = ("produto", "tipo", "area", "criticidade", "cliente"),
    where: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Valores existentes no índice para cada campo categórico (usados no prompt e na tela)."""
    col = get_collection()
    total = col.count()
    if total == 0:
        return {c: [] for c in campos}
    res = col.get(include=["metadatas"], limit=total, where=where)
    out: dict[str, set[str]] = {c: set() for c in campos}
    for md in res["metadatas"] or []:
        for c in campos:
            v = md.get(c)
            if v:
                out[c].add(str(v))
    return {c: sorted(v) for c, v in out.items()}
