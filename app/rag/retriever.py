"""Busca de chamados similares no Chroma."""
from __future__ import annotations

import re

from app.config import get_settings
from app.ingest.builder import BLOCO_ATENDIMENTO, BLOCO_DESCRICAO, BLOCO_OBS, texto_consulta
from app.rag.embeddings import embed_texto
from app.rag.schemas import ChamadoSimilar
from app.rag.store import get_collection

_BLOCOS = (BLOCO_DESCRICAO, BLOCO_ATENDIMENTO, BLOCO_OBS)


def _extrair_bloco(texto: str, label: str) -> str | None:
    """Recupera uma seção do documento indexado (ver builder.montar_texto)."""
    m = re.search(re.escape(label) + r"\n(.*?)(?=\n(?:" + "|".join(re.escape(b) for b in _BLOCOS) + r")|\Z)",
                  texto, flags=re.S)
    if not m:
        return None
    s = m.group(1).strip()
    return s or None


def buscar_similares(
    assunto: str,
    descricao: str | None = None,
    produto: str | None = None,
    top_k: int | None = None,
    excluir_processos: list[int | str] | None = None,
) -> tuple[str, list[ChamadoSimilar]]:
    """`excluir_processos` aceita números (exclui o processo em qualquer origem) ou ids de
    documento como "h8359"/"H8359" (exclui só aquele documento)."""
    s = get_settings()
    k = min(top_k or s.top_k_default, s.top_k_max)
    consulta = texto_consulta(assunto, descricao)
    col = get_collection()
    if col.count() == 0:
        return consulta, []

    where = {"produto": produto} if produto else None
    n = min(col.count(), k * 3 + 5)  # margem para o pós-filtro
    res = col.query(
        query_embeddings=[embed_texto(consulta)],
        n_results=n,
        where=where,
        include=["metadatas", "documents", "distances"],
    )

    ignorados = set(s.status_ignorados)
    excluir_num: set[int] = set()
    excluir_ids: set[str] = set()
    for e in excluir_processos or []:
        if isinstance(e, int) or (isinstance(e, str) and e.isdigit()):
            excluir_num.add(int(e))
        elif isinstance(e, str):
            excluir_ids.add(e.strip().lower())
    out: list[ChamadoSimilar] = []
    for id_, md, doc, dist in zip(res["ids"][0], res["metadatas"][0], res["documents"][0], res["distances"][0]):
        if id_.lower() in excluir_ids or int(md["processo"]) in excluir_num:
            continue
        if (md.get("status") or "") in ignorados:
            continue
        out.append(
            ChamadoSimilar(
                processo=int(md["processo"]),
                origem=md.get("origem", "lecom"),
                assunto=md.get("assunto", ""),
                similaridade=round(1 - float(dist), 4),
                produto=md.get("produto", ""),
                tipo=md.get("tipo", ""),
                area=md.get("area", ""),
                cliente=md.get("cliente", ""),
                status=md.get("status", ""),
                abertura=md.get("abertura", ""),
                tem_resolucao=bool(md.get("tem_resolucao")),
                resposta_analista=_extrair_bloco(doc, BLOCO_ATENDIMENTO),
                descricao_cliente=_extrair_bloco(doc, BLOCO_DESCRICAO),
            )
        )
        if len(out) >= k:
            break
    return consulta, out
