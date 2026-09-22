"""Ingestão incremental: lê as fontes, embeda só o que é novo ou mudou, faz upsert no Chroma.

**A fonte padrão é a raspagem do workspace** (`data/raw/*.json`), que traz o diálogo completo
com autor, data e etapa de cada mensagem.

As planilhas de `EXPORTS_PATH` ficam de fora por padrão: os campos delas são despadronizados
(o mesmo campo muda de dono conforme a etapa e não há data por mensagem). Elas continuam
disponíveis com `com_planilhas=True`, e nesse caso são processadas ANTES da raspagem, que
prevalece quando o mesmo chamado aparece nas duas.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingest.builder import Documento, montar_documento
from app.ingest.loader import list_exports, load_chamados
from app.ingest.raspagem import load_raspagem
from app.rag.embeddings import embed_textos
from app.rag.store import get_collection, recriar_collection, salvar_meta_ingestao
from app.scraper.config import RAW_DIR

log = logging.getLogger(__name__)

UPSERT_BATCH = 200


def _hashes_existentes(col) -> dict[str, str]:
    total = col.count()
    if total == 0:
        return {}
    res = col.get(include=["metadatas"], limit=total)
    return {i: (md or {}).get("hash", "") for i, md in zip(res["ids"], res["metadatas"] or [])}


def _verificar_indice(col, embedding: list[float]) -> None:
    """Força a materialização do índice HNSW e confirma que ele responde antes de o processo
    encerrar. O Chroma grava o índice de forma assíncrona; sem isso, um encerramento rápido
    pode deixar o diretório do índice incompleto ("Error loading hnsw index")."""
    import time

    ultimo_erro: Exception | None = None
    for tentativa in range(5):
        try:
            res = col.query(query_embeddings=[embedding], n_results=1, include=["metadatas"])
            log.info("Índice verificado: %d documentos, consulta ok (%s)", col.count(), res["ids"][0][:1])
            return
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            log.warning("Índice ainda não disponível (tentativa %d): %s", tentativa + 1, str(e)[:120])
            time.sleep(2 * (tentativa + 1))
    raise RuntimeError(f"Índice vetorial não respondeu após a ingestão: {ultimo_erro}")


def ingerir(arquivo: str | Path | None = None, forcar: bool = False,
            com_planilhas: bool = False, limpar: bool = False) -> dict[str, Any]:
    """Indexa a raspagem (padrão) e, opcionalmente, as planilhas de EXPORTS_PATH.

    `arquivo` indexa uma planilha específica e desliga a raspagem.
    `limpar` recria o índice do zero, descartando o que já estava indexado.
    """
    s = get_settings()
    usar_raspagem = True
    if arquivo:
        arquivos = [Path(arquivo)]
        if not arquivos[0].exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {arquivos[0]}")
        usar_raspagem = False
    else:
        arquivos = list_exports(s.exports_dir) if com_planilhas else []

    tem_raspagem = usar_raspagem and any(RAW_DIR.glob("*.json"))
    if not arquivos and not tem_raspagem:
        raise FileNotFoundError(
            f"Nenhuma fonte encontrada. Rode a raspagem (scripts/raspar_lecom.py) "
            f"ou use --com-planilhas com um .xlsx em {s.exports_dir}."
        )

    # o mesmo doc_id pode vir de mais de uma fonte: a processada por último prevalece
    docs: dict[str, Documento] = {}
    por_arquivo: list[dict[str, Any]] = []
    for path in arquivos:
        chamados = load_chamados(path)
        for c in chamados:
            docs[c.doc_id] = montar_documento(c)
        por_arquivo.append({"arquivo": path.name, "chamados": len(chamados)})

    if usar_raspagem:
        raspados = load_raspagem(RAW_DIR)
        for c in raspados:
            docs[c.doc_id] = montar_documento(c)
        por_arquivo.append({"arquivo": "raspagem (data/raw)", "chamados": len(raspados)})

    col = recriar_collection() if limpar else get_collection()
    existentes = {} if limpar else _hashes_existentes(col)

    novos: list[Documento] = []
    atualizados: list[Documento] = []
    ignorados = 0
    for d in docs.values():
        atual = existentes.get(d.id)
        if atual is None:
            novos.append(d)
        elif forcar or atual != d.hash:
            atualizados.append(d)
        else:
            ignorados += 1

    pendentes = novos + atualizados
    log.info("Ingestão de %d arquivo(s): %d lidos | %d novos | %d atualizados | %d sem alteração",
             len(arquivos), len(docs), len(novos), len(atualizados), ignorados)

    if pendentes:
        embeddings = embed_textos([d.texto for d in pendentes])
        for i in range(0, len(pendentes), UPSERT_BATCH):
            lote = pendentes[i : i + UPSERT_BATCH]
            col.upsert(
                ids=[d.id for d in lote],
                documents=[d.texto for d in lote],
                metadatas=[d.metadata for d in lote],
                embeddings=embeddings[i : i + UPSERT_BATCH],
            )
        _verificar_indice(col, embeddings[0])

    resultado = {
        "arquivo": ", ".join(a["arquivo"] for a in por_arquivo),
        "arquivos": por_arquivo,
        "limpou_indice": limpar,
        "lidos": len(docs),
        "novos": len(novos),
        "atualizados": len(atualizados),
        "ignorados": ignorados,
        "total_indexado": col.count(),
    }
    salvar_meta_ingestao(resultado)
    return resultado
