from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_admin_user, get_current_user
from app.config import get_settings
from app.ingest.service import ingerir
from app.rag import feedback as feedback_repo
from app.rag.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    IngestRequest,
    IngestResponse,
    StatsResponse,
    SugestaoRequest,
    SugestaoResponse,
)
from app.rag.service import sugerir
from app.rag.store import get_collection, ler_meta_ingestao, valores_distintos
from app.rate_limit import enforce_rate_limit
from app.users.models import User

log = logging.getLogger(__name__)
router = APIRouter(tags=["Chamados"])


@router.get("/health", summary="Verificação de disponibilidade (pública)")
def health():
    return {"status": "ok"}


@router.get("/stats", response_model=StatsResponse)
def stats(_: User = Depends(get_current_user)):
    s = get_settings()
    return StatsResponse(
        total_indexado=get_collection().count(),
        ultima_ingestao=ler_meta_ingestao(),
        modelo_chat=s.openai_chat_model,
        modelo_embedding=s.openai_embedding_model,
        valores=valores_distintos(),
    )


@router.post("/sugerir", response_model=SugestaoResponse,
             summary="Sugestões para um chamado que está sendo aberto")
def sugerir_endpoint(req: SugestaoRequest, usuario: User = Depends(enforce_rate_limit)):
    if get_collection().count() == 0:
        raise HTTPException(409, "Índice vazio. Rode a ingestão (POST /ingest) antes.")
    try:
        resp = sugerir(req)
    except RuntimeError as e:  # ex.: OPENAI_API_KEY ausente
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("Falha ao gerar sugestão para %s", usuario.username)
        raise HTTPException(502, f"Falha ao consultar o modelo: {e}") from e
    try:
        feedback_repo.registrar_sugestao(
            get_settings().db_path, resp.id_sugestao, usuario.username, req.assunto,
            resp.classificacao.produto, resp.recomendacao, resp.classificacao.confianca,
            [c.rotulo for c in resp.chamados_similares])
    except Exception:  # noqa: BLE001  (sem registro só perde o feedback, não a sugestão)
        log.exception("Falha ao registrar a sugestão %s", resp.id_sugestao)
    return resp


@router.post("/feedback", response_model=FeedbackResponse,
             summary="Informa se a resposta da IA foi útil")
def feedback_endpoint(req: FeedbackRequest, usuario: User = Depends(get_current_user)):
    db = get_settings().db_path
    sugestao = feedback_repo.buscar_sugestao(db, req.id_sugestao)
    # quem não gerou a sugestão recebe o mesmo 404 de um id inexistente: não confirma ids alheios
    if not sugestao or (sugestao["usuario"] != usuario.username and not usuario.is_admin):
        raise HTTPException(404, "Sugestão não encontrada")
    comentario = (req.comentario or "").strip() or None
    feedback_repo.registrar_feedback(db, req.id_sugestao, req.util, comentario, usuario.username)
    return FeedbackResponse(id_sugestao=req.id_sugestao, util=req.util)


@router.get("/feedback/relatorio", summary="Taxa de respostas úteis e comentários negativos (admin)")
def feedback_relatorio(dias: int = Query(30, ge=1, le=365), _: User = Depends(get_admin_user)):
    return feedback_repo.relatorio(get_settings().db_path, dias=dias)


@router.post("/ingest", response_model=IngestResponse, summary="Reindexa a base (admin)")
def ingest_endpoint(req: IngestRequest | None = None, _: User = Depends(get_admin_user)):
    req = req or IngestRequest()
    try:
        r = ingerir(req.arquivo, forcar=req.forcar,
                    com_planilhas=req.com_planilhas, limpar=req.limpar)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na ingestão")
        raise HTTPException(500, f"Falha na ingestão: {e}") from e
    meta = ler_meta_ingestao() or {}
    return IngestResponse(**r, quando=meta.get("quando"))
