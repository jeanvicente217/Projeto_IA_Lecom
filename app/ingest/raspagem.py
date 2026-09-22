"""Converte os JSON da raspagem do workspace (data/raw) em Chamado.

A raspagem é a fonte mais rica: além dos campos do formulário, traz o histórico de cada
campo com autor, data e etapa. Com isso o diálogo do atendimento é remontado em ordem
cronológica real, o que os exports em planilha não permitem.

Papéis:
  Cliente  -> quem abriu o chamado (o solicitante) ou a etapa de Abertura
  N1       -> pedido de informação (campo CT_FALTA_INFO) e observações internas
  Analista -> demais respostas da equipe Nexum
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from app.ingest.loader import Chamado, _clean, _norm_area, _norm_tipo, _texto_util

log = logging.getLogger(__name__)

# campo do formulário -> atributo do Chamado
CAMPOS = {
    "LT_ASSUNTO": "assunto",
    "LST_TIPO_PRODUTO": "produto",
    "LST_PRODUTO": "tipo",
    "LST_RESP": "area",
    "LST_NIVEL_CRITICIDADE": "criticidade",
    "LT_COOPERATIVA2": "cliente",
    "RB_AMBIENTE": "ambiente",
    "RB_SATISFACAO": "satisfacao",
    "LT_NOME": "solicitante",
    "RB_ANALISE": "resolvido",
    "CT_DECREVA_SOLICITACAO": "descricao_solicitacao",
    "CT_PROCESSO_AUTO": "descricao_processo",
    "CT_DETALHAMENTO_CHAMADO": "resposta_analista",
    "CT_OBS_SQUAD": "observacao_interna",
    "CT_MOTIVO": "motivo",
}

# campo do histórico -> papel fixo (quando não depende de quem escreveu)
PAPEL_POR_CAMPO = {
    "CT_FALTA_INFO": "N1",
    "CT_OBS_SQUAD": "N1 (interno)",
    "CT_MOTIVO": "Cliente (avaliação)",
    "CT_PARECER": "Gestor",
    "CT_PARECER_MELHORIA": "Gestor",
}
CAMPOS_DIALOGO = ("CT_DECREVA_SOLICITACAO", "CT_DETALHAMENTO_CHAMADO", "CT_FALTA_INFO",
                  "CT_OBS_SQUAD", "CT_MOTIVO", "CT_PARECER", "CT_PARECER_MELHORIA")

STATUS_CONHECIDOS = ("Aprovado", "Cancelado", "Rejeitado", "Em andamento", "Em alerta", "Atrasado")


def _status_do_cabecalho(cabecalho: str) -> str | None:
    for s in STATUS_CONHECIDOS:
        if s in (cabecalho or ""):
            return s
    return None


def _data(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _papel(campo: str, responsavel: str, solicitante: str, atividade: str) -> str:
    if campo in PAPEL_POR_CAMPO:
        return PAPEL_POR_CAMPO[campo]
    resp = (responsavel or "").strip().casefold()
    if resp and resp == (solicitante or "").strip().casefold():
        return "Cliente"
    if "abertura" in (atividade or "").casefold():
        return "Cliente"
    return "Analista"


def chamado_de_json(dados: dict) -> Chamado | None:
    """Converte um JSON da raspagem em Chamado. Devolve None se não houver conteúdo útil."""
    if dados.get("erro") or dados.get("pulado"):
        return None

    campos = dados.get("campos") or {}
    valores = {attr: _clean(campos.get(col)) for col, attr in CAMPOS.items()}
    solicitante = valores.get("solicitante") or ""

    # diálogo: todos os itens de histórico, de todos os campos, em ordem cronológica
    eventos = []
    for bloco in dados.get("historico") or []:
        campo = bloco["campo"]
        if campo not in CAMPOS_DIALOGO:
            continue
        for item in bloco["itens"]:
            texto = _texto_util(item.get("texto"))
            if not texto:
                continue
            atividade = re.sub(r"\s+", " ", item.get("atividade") or "").strip()
            eventos.append((
                _data(item.get("data")) or datetime.max,
                _papel(campo, item.get("responsavel", ""), solicitante, atividade),
                texto,
            ))
    eventos.sort(key=lambda e: e[0])

    historico: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for _, papel, texto in eventos:
        if texto in vistos:
            continue
        vistos.add(texto)
        historico.append((papel, texto))

    assunto = valores.get("assunto")
    primeira_cliente = next((t for p, t in historico if p == "Cliente"), None)
    if not assunto:
        base = primeira_cliente or valores.get("descricao_solicitacao")
        if not base:
            return None
        assunto = re.split(r"[\n.!?]", base, maxsplit=1)[0].strip()[:120] or base[:120]
    if not historico and not valores.get("descricao_solicitacao"):
        return None

    detalhes = dados.get("detalhes") or {}
    chamado = Chamado(
        processo=int(dados["processo"]),
        assunto=assunto,
        origem="lecom",
        abertura=_data(detalhes.get("processStartDate")),
        status=_status_do_cabecalho(dados.get("cabecalho", "")) or "Aprovado",
        atividade=_clean(detalhes.get("activityTitle")),
        historico=historico,
    )
    for attr, valor in valores.items():
        if attr in ("assunto",):
            continue
        setattr(chamado, attr, valor)
    chamado.tipo = _norm_tipo(chamado.tipo)
    chamado.area = _norm_area(chamado.area)
    # a descrição do cliente vem do histórico quando o campo do formulário está vazio
    if not chamado.descricao_solicitacao and primeira_cliente:
        chamado.descricao_solicitacao = primeira_cliente
    if not chamado.resposta_analista:
        analista = [t for p, t in historico if p == "Analista"]
        chamado.resposta_analista = analista[-1] if analista else None
    return chamado


def load_raspagem(pasta: str | Path) -> list[Chamado]:
    """Lê todos os JSON de data/raw e devolve os chamados aproveitáveis."""
    pasta = Path(pasta)
    arquivos = sorted(pasta.glob("*.json"))
    chamados, vazios, ignorados = [], 0, 0
    for p in arquivos:
        try:
            dados = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            ignorados += 1
            continue
        if dados.get("erro") or dados.get("pulado"):
            ignorados += 1
            continue
        c = chamado_de_json(dados)
        if c is None:
            vazios += 1
            continue
        chamados.append(c)
    log.info("Raspagem %s: %d arquivos, %d chamados, %d sem conteúdo, %d pulados/erro",
             pasta.name, len(arquivos), len(chamados), vazios, ignorados)
    return chamados
