from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_admin_user, get_current_user
from app.config import get_settings
from app.ingest.service import ingerir
from app.rag.schemas import (
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
        return sugerir(req)
    except RuntimeError as e:  # ex.: OPENAI_API_KEY ausente
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("Falha ao gerar sugestão para %s", usuario.username)
        raise HTTPException(502, f"Falha ao consultar o modelo: {e}") from e


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
