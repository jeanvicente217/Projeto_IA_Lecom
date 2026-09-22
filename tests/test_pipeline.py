"""Pipeline completo (ingestão -> Chroma -> busca -> API) sem chamar a OpenAI.

Embeddings são substituídos por um vetor determinístico (hashing de tokens) e o modelo de
chat por uma resposta fixa. Usa um diretório temporário para o Chroma.
"""
import hashlib
import math
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = ROOT / "data" / "exports"
DIM = 4096


def fake_embed(textos):
    """Bag-of-words (unigramas + bigramas) com hashing. Determinístico e sem rede."""
    out = []
    for t in textos:
        v = [0.0] * DIM
        toks = re.findall(r"\w+", t.lower())
        for tok in toks + [a + " " + b for a, b in zip(toks, toks[1:])]:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture(scope="module")
def ambiente(tmp_path_factory):
    from app.ingest.loader import list_exports

    if not list_exports(EXPORTS):
        pytest.skip("nenhum xlsx em data/exports")

    tmp = tmp_path_factory.mktemp("chroma")
    os.environ["CHROMA_PATH"] = str(tmp)
    os.environ["EXPORTS_PATH"] = str(EXPORTS)
    os.environ["OPENAI_API_KEY"] = "fake"
    os.environ["DATABASE_PATH"] = str(tmp / "users.db")
    os.environ["JWT_SECRET"] = "y" * 48
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "senha-de-teste-123456"
    os.environ["PRODUCAO"] = "false"

    from app import config
    from app.rag import store

    config.get_settings.cache_clear()
    store.get_client.cache_clear()

    # A raspagem pode estar rodando e gravando em data/raw durante os testes; uma cópia
    # congelada garante que a contagem não muda no meio de um teste.
    import shutil

    from app.ingest import service as ingest_service
    from app.scraper.config import RAW_DIR

    snapshot = tmp / "raw"
    shutil.copytree(RAW_DIR, snapshot)
    mp = pytest.MonkeyPatch()
    mp.setattr(ingest_service, "RAW_DIR", snapshot)
    yield snapshot
    mp.undo()
    config.get_settings.cache_clear()
    store.get_client.cache_clear()


@pytest.fixture(scope="module")
def chamados(ambiente):
    """Fontes que a ingestão usa por padrão: apenas a raspagem (cópia congelada)."""
    from app.ingest.raspagem import load_raspagem

    raspados = load_raspagem(ambiente)
    if not raspados:
        pytest.skip("sem raspagem em data/raw")
    return {"raspagem": raspados}


@pytest.fixture(autouse=True)
def mocks(monkeypatch, ambiente, chamados):
    from app.ingest import service as ingest_service
    from app.rag import retriever, service as rag_service
    from app.rag.schemas import Classificacao, SaidaModelo

    monkeypatch.setattr(ingest_service, "embed_textos", fake_embed)
    monkeypatch.setattr(retriever, "embed_texto", lambda t: fake_embed([t])[0])

    def fake_modelo(system, user):
        assert "VALORES PERMITIDOS" in system
        assert "CHAMADO QUE ESTÁ SENDO ABERTO" in user
        return SaidaModelo(
            classificacao=Classificacao(
                produto="NxPro (Plataforma Lecom)", tipo_chamado="Informe de erro",
                area_sugerida="N2 - Phoenix", criticidade_sugerida="Alto", confianca=0.8,
            ),
            dicas=["Teste (ref. chamado 1)"],
            mensagem_para_cliente="Antes de abrir, tente reenviar o anexo.",
            resumo_para_n1="resumo",
            informacoes_faltantes=[],
            recomendacao="abrir_chamado",
            justificativa="teste",
        )

    monkeypatch.setattr(rag_service, "_chamar_modelo", fake_modelo)


def test_ingestao_e_idempotencia(chamados):
    from app.ingest.service import ingerir
    from app.rag.store import get_collection

    esperado = len({c.doc_id for cs in chamados.values() for c in cs})
    r1 = ingerir()
    assert r1["novos"] == esperado
    assert r1["total_indexado"] == esperado
    assert get_collection().count() == esperado
    assert r1["lidos"] == esperado

    r2 = ingerir()
    assert r2["novos"] == 0 and r2["atualizados"] == 0
    assert r2["ignorados"] == esperado


def test_planilhas_so_entram_quando_pedido(monkeypatch):
    """Por padrão a ingestão nem olha para data/exports; as planilhas exigem com_planilhas."""
    from app.ingest import service as ingest_service

    chamadas: list[str] = []
    monkeypatch.setattr(ingest_service, "list_exports",
                        lambda d: chamadas.append(str(d)) or [])

    r = ingest_service.ingerir()
    assert chamadas == [], "sem com_planilhas, os exports não devem ser lidos"
    assert [a["arquivo"] for a in r["arquivos"]] == ["raspagem (data/raw)"]

    ingest_service.ingerir(com_planilhas=True)
    assert len(chamadas) == 1, "com com_planilhas, os exports devem ser procurados"


def test_limpar_recria_o_indice(chamados):
    from app.ingest.service import ingerir
    from app.rag.store import get_collection

    esperado = len({c.doc_id for cs in chamados.values() for c in cs})
    get_collection().upsert(ids=["lixo-antigo"], documents=["resto de ingestão anterior"],
                            metadatas=[{"processo": 0, "hash": "x"}],
                            embeddings=fake_embed(["resto"]))
    assert get_collection().count() == esperado + 1

    r = ingerir(limpar=True)
    assert r["limpou_indice"] is True
    assert r["novos"] == esperado and r["ignorados"] == 0
    assert get_collection().count() == esperado, "o documento antigo deve ter sumido"


def test_busca_similares_encontra_documento_igual(chamados):
    from app.ingest.builder import montar_texto
    from app.ingest.service import ingerir
    from app.rag.retriever import buscar_similares
    from app.rag.store import get_collection

    if get_collection().count() == 0:  # permite rodar este teste isolado
        ingerir()

    todos = [c for cs in chamados.values() for c in cs]
    candidatos = [c for c in todos
                  if c.tem_resolucao and c.status in ("Aprovado", "Finalizado")
                  and len(c.assunto) > 25]
    # o alvo é o de texto mais longo: vocabulário rico o bastante para ser inconfundível
    # sob o embedding falso (saco de palavras), que não distingue textos curtos e genéricos
    alvo = max(candidatos, key=lambda c: len(montar_texto(c)))

    consulta, sims = buscar_similares(alvo.assunto, montar_texto(alvo), top_k=5)
    assert consulta.startswith("Assunto: ")
    # o índice HNSW é aproximado: com o embedding falso o alvo exato pode não ser o 1º,
    # mas tem que estar entre os primeiros para a busca ser considerada saudável
    top3 = [(s.processo, s.origem) for s in sims[:3]]
    assert (alvo.processo, alvo.origem) in top3, top3
    assert sims[0].tem_resolucao and sims[0].resposta_analista
    assert all(s.status not in ("Cancelado", "Rejeitado") for s in sims)

    # exclusão do próprio chamado (usado na avaliação leave-one-out): por id ou por número
    _, sims2 = buscar_similares(alvo.assunto, montar_texto(alvo), top_k=5, excluir_processos=[alvo.doc_id.upper()])
    assert all((s.processo, s.origem) != (alvo.processo, alvo.origem) for s in sims2)
    _, sims3 = buscar_similares(alvo.assunto, montar_texto(alvo), top_k=5, excluir_processos=[alvo.processo])
    assert all(s.processo != alvo.processo for s in sims3)


def test_filtro_por_produto():
    from app.rag.retriever import buscar_similares

    _, sims = buscar_similares("Erro no robô", top_k=5, produto="NxBot (RPA)")
    assert sims and all(s.produto == "NxBot (RPA)" for s in sims)


def test_api_endpoints():
    import importlib

    from fastapi.testclient import TestClient

    import app.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200

        # sem credencial as rotas de dados ficam fechadas
        assert client.get("/stats").status_code == 401

        token = client.post("/auth/login", json={"username": "admin",
                                                 "password": "senha-de-teste-123456"}).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        st = client.get("/stats", headers=h).json()
        assert st["total_indexado"] > 100
        assert "NxPro (Plataforma Lecom)" in st["valores"]["produto"]
        assert st["ultima_ingestao"]["quando"]

        r = client.post("/sugerir", headers=h, json={"assunto": "Erro ao abrir subprocesso",
                                                     "descricao": "tela em branco", "top_k": 4})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["classificacao"]["produto"] == "NxPro (Plataforma Lecom)"
        assert len(body["chamados_similares"]) == 4
        assert {"origem", "processo"} <= set(body["chamados_similares"][0])
        assert body["consulta"].startswith("Assunto: Erro ao abrir subprocesso")
        assert body["mensagem_para_cliente"]

        assert client.post("/sugerir", headers=h, json={"assunto": "ab"}).status_code == 422

        r = client.post("/ingest", headers=h, json={})
        assert r.status_code == 200 and r.json()["novos"] == 0


def test_prompt_customizado_chega_ao_modelo(monkeypatch):
    """instrucoes_extras entram no prompt e as regras fixas continuam presentes."""
    from app.rag import service as rag_service
    from app.rag.schemas import SugestaoRequest

    capturado = {}

    def espiao(system, user):
        capturado["system"] = system
        return _saida_fake()

    monkeypatch.setattr(rag_service, "_chamar_modelo", espiao)

    r = rag_service.sugerir(SugestaoRequest(
        assunto="Erro ao anexar documento",
        instrucoes_extras="Escreva a mensagem ao cliente em no máximo 3 linhas."))
    assert "no máximo 3 linhas" in capturado["system"]
    assert "REGRAS FIXAS" in capturado["system"]
    assert "VALORES PERMITIDOS" in capturado["system"], "o prompt padrão deve continuar"
    assert r.prompt_customizado is True

    rag_service.sugerir(SugestaoRequest(assunto="Erro ao anexar documento",
                                        prompt_sistema="Responda apenas com a classificação."))
    assert "Responda apenas com a classificação." in capturado["system"]
    assert "REGRAS FIXAS" in capturado["system"], "as regras fixas valem mesmo substituindo"
    assert "VALORES PERMITIDOS" not in capturado["system"]


def _saida_fake():
    from app.rag.schemas import Classificacao, SaidaModelo

    return SaidaModelo(
        classificacao=Classificacao(produto="NxPro (Plataforma Lecom)", tipo_chamado="Suporte",
                                    area_sugerida="N2 - Kaizen", criticidade_sugerida="Médio",
                                    confianca=0.5),
        mensagem_para_cliente="Tente reenviar o anexo.", resumo_para_n1="resumo",
        recomendacao="abrir_chamado")
