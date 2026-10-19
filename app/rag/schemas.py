from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


MAX_TEXTO = 20_000
MAX_INSTRUCOES = 4_000


class SugestaoRequest(BaseModel):
    assunto: str = Field(..., min_length=3, max_length=500,
                         description="Assunto do chamado que está sendo aberto")
    descricao: str | None = Field(None, max_length=MAX_TEXTO,
                                  description="Descrição livre feita pelo cliente")
    produto: str | None = Field(None, max_length=120,
                                description="Produto informado pelo cliente (filtra a busca)")
    cliente: str | None = Field(None, max_length=200,
                                description="Cooperativa/empresa do solicitante (detecta duplicados)")
    top_k: int | None = Field(None, ge=1, description="Quantos chamados similares recuperar")
    instrucoes_extras: str | None = Field(
        None, max_length=MAX_INSTRUCOES,
        description="Instruções acrescentadas ao prompt padrão, por exemplo para ajustar o tom "
                    "das dicas ou pedir foco num produto. Não substitui as regras de segurança.")
    prompt_sistema: str | None = Field(
        None, max_length=MAX_INSTRUCOES,
        description="Substitui por completo o prompt padrão. Uso avançado: as regras de "
                    "fundamentação das dicas deixam de valer, então prefira instrucoes_extras.")
    excluir_processos: list[int | str] = Field(
        default_factory=list,
        description="Chamados a ignorar na busca (uso em avaliação): nº do processo ou id como 'h8359'",
    )


class Classificacao(BaseModel):
    produto: str = Field(..., description="Um dos produtos existentes no histórico")
    tipo_chamado: str = Field(..., description="Informe de erro | Suporte | Melhoria")
    area_sugerida: str = Field(..., description="Área responsável (N1/N2/N3 ...) mais provável")
    criticidade_sugerida: str = Field(..., description="Baixo | Médio | Alto | Crítico")
    confianca: float = Field(..., ge=0, le=1)


Recomendacao = Literal["abrir_chamado", "tentar_dica_antes", "possivel_duplicado"]


class SaidaModelo(BaseModel):
    """Parte da resposta gerada pelo modelo de linguagem (structured output)."""
    classificacao: Classificacao
    dicas: list[str] = Field(default_factory=list,
                             description="Orientações para o cliente; nunca citam nº de chamado")
    mensagem_para_cliente: str = Field(
        "", description="Texto pronto para exibir ao cliente no processo, antes de ele abrir o "
                        "chamado. Sempre preenchido, mesmo quando não há dicas. Nunca cita "
                        "nº de chamado (são internos).")
    resumo_para_n1: str
    informacoes_faltantes: list[str] = Field(default_factory=list)
    recomendacao: Recomendacao
    justificativa: str = Field("", description="Por que a recomendação foi dada, citando nº de processos")


class ChamadoSimilar(BaseModel):
    processo: int
    origem: str = "lecom"  # "lecom" = nº do processo Lecom | "historico" = id do formulário (2024-2025)
    assunto: str
    similaridade: float
    produto: str = ""
    tipo: str = ""
    area: str = ""
    cliente: str = ""
    status: str = ""
    abertura: str = ""
    tem_resolucao: bool = False
    resposta_analista: str | None = Field(None, description="Diálogo do atendimento (N1/Analista/Cliente)")
    descricao_cliente: str | None = None

    @property
    def rotulo(self) -> str:
        return str(self.processo) if self.origem == "lecom" else f"H{self.processo}"


class SugestaoResponse(SaidaModelo):
    id_sugestao: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Identificador desta resposta; devolva-o em POST /feedback")
    chamados_similares: list[ChamadoSimilar]
    modelo: str
    consulta: str
    prompt_customizado: bool = Field(
        False, description="Indica que a requisição alterou o prompt padrão")


class FeedbackRequest(BaseModel):
    id_sugestao: str = Field(..., min_length=8, max_length=64,
                             description="O id_sugestao devolvido pelo /sugerir")
    util: bool = Field(..., description="A resposta da IA ajudou o cliente?")
    comentario: str | None = Field(None, max_length=1000,
                                   description="Opcional: o que faltou ou o que ajudou")


class FeedbackResponse(BaseModel):
    id_sugestao: str
    util: bool
    registrado: bool = True


class IngestRequest(BaseModel):
    arquivo: str | None = Field(None, description="Indexa uma planilha específica (desliga a raspagem)")
    com_planilhas: bool = Field(False, description="Inclui os .xlsx de EXPORTS_PATH além da raspagem")
    limpar: bool = Field(False, description="Recria o índice do zero")
    forcar: bool = False


class IngestResponse(BaseModel):
    arquivo: str
    arquivos: list[dict] = Field(default_factory=list)
    limpou_indice: bool = False
    lidos: int
    novos: int
    atualizados: int
    ignorados: int
    total_indexado: int
    quando: str | None = None


class StatsResponse(BaseModel):
    total_indexado: int
    ultima_ingestao: dict | None
    modelo_chat: str
    modelo_embedding: str
    valores: dict[str, list[str]]
