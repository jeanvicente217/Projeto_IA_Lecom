"""Rotas do WhatsApp.

  POST /whatsapp/webhook/{token}   <- chamado pela Evolution API (público, protegido por token)
  POST /whatsapp/simular           <- admin: testa uma mensagem sem WhatsApp de verdade
  GET/POST/DELETE /whatsapp/contatos  <- admin: cadastro dos telefones autorizados
  GET  /whatsapp/status            <- admin
  POST /whatsapp/indice/atualizar  <- admin: força reindexar data/raw
  GET/PATCH /whatsapp/pre-chamados <- admin: fila de descrições recebidas por autoatendimento
  POST /whatsapp/notificar         <- admin: dispara os avisos de mudança de status (agende)
  POST /whatsapp/resumo-semanal    <- admin: dispara o resumo semanal dos gestores (agende)

Camadas de proteção do webhook, nesta ordem:
  1. token secreto na URL (só quem configurou o Evolution o conhece)
  2. `apikey` da instância no corpo, quando presente, tem que bater com a configurada
  3. mensagens de grupo ou enviadas por nós mesmos são ignoradas
  4. deduplicação pelo id da mensagem (a Evolution reenvia em caso de timeout)
  5. só telefone cadastrado e ativo recebe QUALQUER resposta — número fora da lista de
     permissão é descartado em silêncio, sem nem a mensagem genérica de "não cadastrado"
  6. limite de mensagens por telefone (só se aplica a quem já passou pelo item 5)
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_admin_user
from app.config import Settings, get_settings
from app.rate_limit import RateLimiter
from app.whatsapp import consulta, indice, repositorio
from app.whatsapp.evolution import EvolutionClient, extrair_mensagem
from app.whatsapp.telefone import mascarar, normalizar

# tipos de mídia que reconhecemos e confirmamos recebimento, sem processar o conteúdo ainda
TIPOS_MIDIA_RECONHECIDOS = {"imageMessage", "documentMessage", "audioMessage", "videoMessage"}
TEXTO_ANEXO_RECEBIDO = (
    "Recebi seu arquivo! Ainda não consigo analisar imagens/áudios automaticamente, mas "
    "deixei registrado aqui na conversa. Se puder, descreva também em texto o que está "
    "acontecendo que eu já te ajudo.")

log = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])
limite_por_telefone = RateLimiter()
JANELA_S = 600


def _idx(settings: Settings) -> Path:
    return Path(settings.db_path).parent / "chamados_idx.db"


def _resposta_para(telefone: str, texto: str, settings: Settings) -> str | None:
    """Decide a resposta já considerando cadastro e limite. Nunca lança.

    Devolve None quando NADA deve ser enviado: número fora da lista de permissão não
    recebe resposta alguma, nem genérica — silêncio total, por decisão explícita.
    """
    contato = repositorio.buscar_contato(settings.db_path, telefone)
    if not contato or not contato.ativo:
        return None
    permitido, _ = limite_por_telefone.check(telefone, settings.whatsapp_msgs_por_10min, JANELA_S)
    if not permitido:
        return consulta.TEXTO_LIMITE
    repositorio.registrar_uso(settings.db_path, telefone)
    try:
        return consulta.responder(texto, contato, settings)
    except Exception:  # noqa: BLE001
        log.exception("falha ao montar resposta para %s", mascarar(telefone))
        return "Tive um problema ao consultar agora. Tente novamente em instantes."


# ----------------------------------------------------------------------------- webhook

@router.post("/webhook/{token}", include_in_schema=False)
async def webhook(token: str, request: Request, tarefas: BackgroundTasks,
                  settings: Settings = Depends(get_settings)):
    # 404 (e não 401/403) para não confirmar que a rota existe
    if not settings.whatsapp_habilitado or not settings.whatsapp_webhook_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not secrets.compare_digest(token, settings.whatsapp_webhook_token):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON inválido")
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "payload inesperado")

    apikey = payload.get("apikey")
    if apikey and settings.evolution_api_key and not secrets.compare_digest(
            str(apikey), settings.evolution_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "apikey não confere")

    msg = extrair_mensagem(payload)
    if not msg or msg["from_me"] or msg["grupo"]:
        return {"ignorado": True}

    telefone = normalizar(msg["jid"])
    if not telefone:
        return {"ignorado": True, "motivo": "telefone"}
    if not repositorio.marcar_evento(settings.db_path, msg["id"], telefone):
        return {"ignorado": True, "motivo": "duplicado"}

    # imagem/documento/áudio/vídeo: confirma o recebimento, sem analisar o conteúdo ainda.
    # Mesma regra de silêncio dos textos: só quem está cadastrado e ativo recebe algo.
    if not msg["texto"]:
        if msg["tipo"] not in TIPOS_MIDIA_RECONHECIDOS:
            return {"ignorado": True}
        contato = repositorio.buscar_contato(settings.db_path, telefone)
        if not contato or not contato.ativo:
            return {"ok": True, "ignorado": True, "motivo": "nao_cadastrado"}
        pendente = repositorio.obter_estado(settings.db_path, telefone)
        if pendente and pendente["tipo"] == "confirmar_pre_chamado":
            dados = pendente["dados"]
            dados["descricao"] = (dados.get("descricao", "") + "\n\n"
                                  "[Cliente enviou um anexo pelo WhatsApp nesta conversa.]")
            repositorio.salvar_estado(settings.db_path, telefone, "confirmar_pre_chamado", dados,
                                      ttl_min=consulta.TTL_CONFIRMACAO_MIN)
        cliente = EvolutionClient(settings)
        tarefas.add_task(cliente.enviar_texto, telefone, TEXTO_ANEXO_RECEBIDO)
        return {"ok": True, "tipo": "anexo"}

    comando, _ = consulta.interpretar(msg["texto"])
    log.info("whatsapp: mensagem de %s (%s)", mascarar(telefone), comando)

    resposta = _resposta_para(telefone, msg["texto"], settings)
    if resposta is None:
        log.info("whatsapp: %s não cadastrado; ignorado em silêncio", mascarar(telefone))
        return {"ok": True, "ignorado": True, "motivo": "nao_cadastrado"}
    cliente = EvolutionClient(settings)
    tarefas.add_task(cliente.enviar_texto, telefone, resposta)
    return {"ok": True}


# ----------------------------------------------------------------------------- admin

class SimularRequest(BaseModel):
    telefone: str = Field(..., description="Número de quem manda, ex.: 14991449422")
    texto: str = Field(..., min_length=1, max_length=500)


class SimularResponse(BaseModel):
    telefone: str
    comando: str
    resposta: str | None = Field(
        None, description="None significa que nada seria enviado (número não cadastrado)")


@router.post("/simular", response_model=SimularResponse, dependencies=[Depends(get_admin_user)],
             summary="Simula uma mensagem recebida e devolve o que o bot responderia (não envia)")
def simular(body: SimularRequest, settings: Settings = Depends(get_settings)):
    telefone = normalizar(body.telefone)
    if not telefone:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Telefone inválido")
    comando, _ = consulta.interpretar(body.texto)
    return SimularResponse(telefone=telefone, comando=comando,
                           resposta=_resposta_para(telefone, body.texto, settings))


class ContatoRequest(BaseModel):
    telefone: str = Field(..., description="DDD + número; o 55 é opcional")
    nome: str = Field(..., min_length=2, max_length=120)
    cooperativa: str = Field(..., min_length=2, max_length=120,
                             description="Exatamente como aparece no campo Cliente do chamado")
    escopo: str = Field("proprio", pattern="^(proprio|cooperativa)$")
    ativo: bool = True


class ContatoResponse(BaseModel):
    telefone: str
    nome: str
    cooperativa: str
    escopo: str
    ativo: bool
    criado_em: str | None = None
    ultimo_uso: str | None = None


def _resp(c: repositorio.Contato) -> ContatoResponse:
    return ContatoResponse(**c.__dict__)


@router.get("/contatos", response_model=list[ContatoResponse],
            dependencies=[Depends(get_admin_user)])
def listar_contatos(settings: Settings = Depends(get_settings)):
    return [_resp(c) for c in repositorio.listar_contatos(settings.db_path)]


@router.post("/contatos", response_model=ContatoResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(get_admin_user)],
             summary="Cadastra ou atualiza um telefone autorizado")
def salvar_contato(body: ContatoRequest, settings: Settings = Depends(get_settings)):
    try:
        c = repositorio.salvar_contato(settings.db_path, body.telefone, body.nome,
                                       body.cooperativa, body.escopo, body.ativo)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return _resp(c)


@router.delete("/contatos/{telefone}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(get_admin_user)])
def remover_contato(telefone: str, settings: Settings = Depends(get_settings)):
    if not repositorio.remover_contato(settings.db_path, telefone):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contato não encontrado")


@router.get("/status", dependencies=[Depends(get_admin_user)])
def status_whatsapp(settings: Settings = Depends(get_settings)):
    cliente = EvolutionClient(settings)
    indice.atualizar(settings.whatsapp_raw_dir, _idx(settings))  # incremental e barato
    return {
        "habilitado": settings.whatsapp_habilitado,
        "evolution_configurada": cliente.configurado(),
        "webhook_token_definido": bool(settings.whatsapp_webhook_token),
        "chamados_indexados": indice.total(_idx(settings)),
        "contatos": len(repositorio.listar_contatos(settings.db_path)),
        "resumo_ia": settings.whatsapp_resumo_ia,
    }


@router.post("/indice/atualizar", dependencies=[Depends(get_admin_user)])
def atualizar_indice(forcar: bool = False, settings: Settings = Depends(get_settings)):
    r = indice.atualizar(settings.whatsapp_raw_dir, _idx(settings), forcar=forcar)
    return {**r, "total": indice.total(_idx(settings))}


# ----------------------------------------------------------------------------- pré-chamados

class PreChamadoResponse(BaseModel):
    id: int
    telefone: str
    nome: str
    cooperativa: str
    assunto: str
    descricao: str
    classificacao: dict[str, Any] | None = None
    origem: str
    status: str
    numero_chamado: str | None = None
    observacao: str | None = None
    criado_em: str
    atualizado_em: str | None = None


class AtualizarPreChamadoRequest(BaseModel):
    status: str = Field(..., pattern="^(pendente|aberto|descartado)$")
    numero_chamado: str | None = Field(None, description="Preencha ao marcar como 'aberto'")
    observacao: str | None = None


@router.get("/pre-chamados", response_model=list[PreChamadoResponse],
            dependencies=[Depends(get_admin_user)],
            summary="Descrições de problema recebidas por autoatendimento, aguardando um humano")
def listar_pre_chamados(status_filtro: str | None = None, settings: Settings = Depends(get_settings)):
    return repositorio.listar_pre_chamados(settings.db_path, status_filtro)


@router.patch("/pre-chamados/{id_}", response_model=PreChamadoResponse,
              dependencies=[Depends(get_admin_user)],
              summary="Marca um pré-chamado como aberto (com o nº real) ou descartado")
def atualizar_pre_chamado(id_: int, body: AtualizarPreChamadoRequest,
                          settings: Settings = Depends(get_settings)):
    if not repositorio.atualizar_pre_chamado(settings.db_path, id_, body.status,
                                             body.numero_chamado, body.observacao):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pré-chamado não encontrado")
    item = repositorio.buscar_pre_chamado(settings.db_path, id_)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pré-chamado não encontrado")
    return item


# ----------------------------------------------------------------------------- proativo

@router.post("/notificar", dependencies=[Depends(get_admin_user)],
             summary="Avisa contatos 'proprio' sobre mudanças de status (agende periodicamente)")
def notificar(settings: Settings = Depends(get_settings)):
    from app.whatsapp.notificacoes import verificar_e_notificar

    return verificar_e_notificar(settings)


@router.post("/resumo-semanal", dependencies=[Depends(get_admin_user)],
             summary="Envia o resumo semanal para contatos com escopo 'cooperativa' (agende 1x/semana)")
def resumo_semanal(settings: Settings = Depends(get_settings)):
    from app.whatsapp.notificacoes import enviar_resumo_semanal

    return enviar_resumo_semanal(settings)
