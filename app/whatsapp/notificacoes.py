"""Mensagens que o bot envia por conta própria, sem o cliente perguntar antes.

Dois tipos:
  verificar_e_notificar   -> avisa um contato "proprio" quando o status de UM chamado dele
                             muda ou o atendimento responde algo novo.
  enviar_resumo_semanal   -> manda uma contagem por status para contatos "cooperativa"
                             (gestores), em vez de um aviso por chamado individual.

Só contatos "proprio" recebem avisos por chamado, porque quem tem escopo "cooperativa"
enxerga TODOS os chamados da empresa: notificar cada mudança de qualquer colega viraria uma
enxurrada de mensagens. Gestores recebem o resumo semanal no lugar.

Nenhuma das duas funções tem agendamento embutido — chame periodicamente via
`POST /whatsapp/notificar` / `POST /whatsapp/resumo-semanal` (admin) ou pelos scripts em
`scripts/`, do mesmo jeito que a ingestão já é agendada (Task Scheduler / cron).
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import Settings
from app.whatsapp import indice, repositorio
from app.whatsapp.evolution import EvolutionClient
from app.whatsapp.telefone import variantes

log = logging.getLogger(__name__)

MAX_CHAMADOS_POR_CONTATO = 100
MAX_CHAMADOS_RESUMO = 500


def _idx(settings: Settings) -> Path:
    return Path(settings.db_path).parent / "chamados_idx.db"


def _hash_linha(linha: dict[str, Any]) -> str:
    """Muda quando o status muda OU quando a raspagem traz atendimento novo (raspado_em)."""
    chave = f"{linha.get('status')}|{linha.get('raspado_em')}"
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:16]


def verificar_e_notificar(settings: Settings) -> dict[str, int]:
    """Para cada contato com escopo 'proprio', avisa se algum chamado dele mudou desde a
    última checagem. Na primeira vez que vê um (contato, chamado), só registra em silêncio
    — não dispara um aviso para todo o histórico existente de uma vez."""
    caminho_idx = _idx(settings)
    indice.atualizar(settings.whatsapp_raw_dir, caminho_idx)
    cliente = EvolutionClient(settings)

    stats = {"contatos": 0, "verificados": 0, "notificados": 0, "primeira_vez": 0, "falhas_envio": 0}
    for contato in repositorio.listar_contatos(settings.db_path):
        if not contato.ativo or contato.escopo != "proprio":
            continue
        stats["contatos"] += 1
        linhas = indice.listar(caminho_idx, cooperativa=contato.cooperativa,
                               telefones=variantes(contato.telefone), solicitante=contato.nome,
                               limite=MAX_CHAMADOS_POR_CONTATO)
        for linha in linhas:
            stats["verificados"] += 1
            processo = linha["processo"]
            novo_hash = _hash_linha(linha)
            anterior = repositorio.hash_notificado(settings.db_path, contato.telefone, processo)
            if anterior == novo_hash:
                continue
            if anterior is None:
                repositorio.marcar_notificado(settings.db_path, contato.telefone, processo, novo_hash)
                stats["primeira_vez"] += 1
                continue
            texto = (f"🔔 Atualização no chamado *{processo}*\n"
                     f"{linha.get('assunto') or ''}\n"
                     f"Novo status: *{linha.get('status') or '-'}*\n\n"
                     "Mande o número do chamado para ver os detalhes.")
            if cliente.enviar_texto(contato.telefone, texto):
                stats["notificados"] += 1
            else:
                stats["falhas_envio"] += 1
            # marca de qualquer forma: evita reenviar em loop se a Evolution estiver fora
            repositorio.marcar_notificado(settings.db_path, contato.telefone, processo, novo_hash)
    log.info("notificações proativas de WhatsApp: %s", stats)
    return stats


def enviar_resumo_semanal(settings: Settings) -> dict[str, int]:
    """Manda, para cada contato com escopo 'cooperativa', a contagem de chamados por status
    da própria cooperativa. Pensado para rodar uma vez por semana."""
    caminho_idx = _idx(settings)
    indice.atualizar(settings.whatsapp_raw_dir, caminho_idx)
    cliente = EvolutionClient(settings)

    stats = {"contatos": 0, "enviados": 0, "sem_chamados": 0, "falhas_envio": 0}
    for contato in repositorio.listar_contatos(settings.db_path):
        if not contato.ativo or contato.escopo != "cooperativa":
            continue
        stats["contatos"] += 1
        linhas = indice.listar(caminho_idx, cooperativa=contato.cooperativa,
                               limite=MAX_CHAMADOS_RESUMO)
        if not linhas:
            stats["sem_chamados"] += 1
            continue
        contagem = Counter(l.get("status") or "sem status" for l in linhas)
        corpo_status = "\n".join(f"• {status}: {n}" for status, n in contagem.most_common())
        texto = (f"📋 *Resumo semanal — {contato.cooperativa}*\n\n"
                 f"{len(linhas)} chamados no histórico recente:\n{corpo_status}\n\n"
                 "Mande *meus chamados* para ver a lista, ou o número de um específico.")
        if cliente.enviar_texto(contato.telefone, texto):
            stats["enviados"] += 1
        else:
            stats["falhas_envio"] += 1
    log.info("resumo semanal de WhatsApp: %s", stats)
    return stats
