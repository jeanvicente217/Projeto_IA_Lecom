"""Transforma um Chamado no documento que vai para o índice vetorial.

- `texto`: o que é embedado e mostrado ao modelo na hora de sugerir.
- `metadata`: campos filtráveis (Chroma aceita apenas str/int/float/bool).
- `hash`: identifica o conteúdo; se não mudar entre ingestões, o chamado não é re-embedado.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.ingest.loader import Chamado

MAX_CHARS = 6000       # limite de segurança para o texto embedado
MAX_MSG_CHARS = 1200   # cada mensagem do diálogo

BLOCO_DESCRICAO = "Descrição do cliente:"
BLOCO_ATENDIMENTO = "Atendimento:"
BLOCO_OBS = "Observações:"


@dataclass
class Documento:
    id: str
    texto: str
    metadata: dict[str, Any]

    @property
    def hash(self) -> str:
        return self.metadata["hash"]


def _linha(label: str, valor: str | None) -> str | None:
    return f"{label}: {valor}" if valor else None


def montar_texto(c: Chamado) -> str:
    cabecalho = " | ".join(
        p for p in [
            _linha("Produto", c.produto),
            _linha("Tipo", c.tipo),
            _linha("Área", c.area),
            _linha("Cliente", c.cliente),
            _linha("Ambiente", c.ambiente),
            _linha("Criticidade", c.criticidade),
        ] if p
    )
    blocos = [f"Assunto: {c.assunto}", cabecalho]
    if c.descricao_cliente:
        blocos.append(f"{BLOCO_DESCRICAO}\n{c.descricao_cliente[:MAX_MSG_CHARS * 2]}")
    atendimento = c.atendimento
    if atendimento:
        # a descrição inicial do cliente já está no bloco acima; evita duplicar
        descricao = c.descricao_cliente
        linhas = []
        for papel, texto in atendimento:
            if papel == "Cliente" and texto[:120] in descricao:
                continue
            linhas.append(f"{papel}: {texto[:MAX_MSG_CHARS]}")
        if linhas:
            blocos.append(f"{BLOCO_ATENDIMENTO}\n" + "\n".join(linhas))
    if c.observacao_interna:
        blocos.append(f"{BLOCO_OBS}\n{c.observacao_interna[:MAX_MSG_CHARS]}")
    texto = "\n".join(b for b in blocos if b)
    return texto[:MAX_CHARS]


def montar_metadata(c: Chamado, texto: str) -> dict[str, Any]:
    md: dict[str, Any] = {
        "processo": c.processo,
        "origem": c.origem,
        "assunto": c.assunto[:500],
        "produto": c.produto or "",
        "tipo": c.tipo or "",
        "area": c.area or "",
        "cliente": c.cliente or "",
        "status": c.status or "",
        "criticidade": c.criticidade or "",
        "ambiente": c.ambiente or "",
        "abertura": c.abertura.isoformat(timespec="seconds") if c.abertura else "",
        "resolvido": c.resolvido or "",
        "satisfacao": c.satisfacao or "",
        "tem_resolucao": c.tem_resolucao,
        "tem_descricao": bool(c.descricao_cliente),
    }
    md["hash"] = hashlib.sha1(texto.encode("utf-8")).hexdigest()
    return md


def montar_documento(c: Chamado) -> Documento:
    texto = montar_texto(c)
    return Documento(id=c.doc_id, texto=texto, metadata=montar_metadata(c, texto))


def texto_consulta(assunto: str, descricao: str | None = None) -> str:
    """Formato da consulta, espelhando o começo do documento indexado."""
    partes = [f"Assunto: {assunto.strip()}"]
    if descricao and descricao.strip():
        partes.append(f"{BLOCO_DESCRICAO}\n{descricao.strip()}")
    return "\n".join(partes)[:MAX_CHARS]
