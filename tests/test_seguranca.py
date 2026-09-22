"""Testes da camada de segurança: autenticação, permissões, rate limit e cabeçalhos.

Não chamam a OpenAI nem o Chroma real: o endpoint /sugerir é substituído por um dublê.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

ADMIN = "admin"
SENHA_ADMIN = "senha-de-teste-123456"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("seg")
    os.environ.update({
        "DATABASE_PATH": str(tmp / "users.db"),
        "CHROMA_PATH": str(tmp / "chroma"),
        "JWT_SECRET": "x" * 48,
        "ADMIN_USERNAME": ADMIN,
        "ADMIN_PASSWORD": SENHA_ADMIN,
        "OPENAI_API_KEY": "fake",
        "PRODUCAO": "false",
        "CORS_ORIGINS": "https://bpm.nexum.com.br",
        "ALLOWED_HOSTS": "*",
        "DEFAULT_RATE_LIMIT_PER_HOUR": "5",
    })
    from app import config
    config.get_settings.cache_clear()

    import app.main as main_mod
    import importlib
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c

    config.get_settings.cache_clear()


@pytest.fixture
def token_admin(client) -> str:
    r = client.post("/auth/login", json={"username": ADMIN, "password": SENHA_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- autenticação ---------------------------------------------------------

def test_health_e_publico(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("metodo,rota", [
    ("get", "/stats"), ("post", "/sugerir"), ("post", "/ingest"),
    ("get", "/auth/me"), ("get", "/usuarios"),
])
def test_rotas_exigem_credencial(client, metodo, rota):
    r = getattr(client, metodo)(rota, **({"json": {"assunto": "teste"}} if metodo == "post" else {}))
    assert r.status_code == 401, f"{rota} respondeu {r.status_code}"


def test_login_invalido_nao_vaza_qual_campo_errou(client):
    r = client.post("/auth/login", json={"username": ADMIN, "password": "errada"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Usuário ou senha inválidos"


def test_token_invalido_recusado(client):
    assert client.get("/auth/me", headers=auth("token.falso.aqui")).status_code == 401


def test_me_devolve_o_usuario(client, token_admin):
    d = client.get("/auth/me", headers=auth(token_admin)).json()
    assert d["username"] == ADMIN and d["is_admin"] is True


# --- API keys -------------------------------------------------------------

def test_api_key_autentica_e_pode_ser_revogada(client, token_admin):
    r = client.post("/auth/api-key", headers=auth(token_admin))
    assert r.status_code == 201
    chave = r.json()["api_key"]
    assert chave.startswith("nxp_")

    r = client.get("/auth/me", headers={"X-API-Key": chave})
    assert r.status_code == 200 and r.json()["username"] == ADMIN

    assert client.delete("/auth/api-key", headers=auth(token_admin)).status_code == 204
    assert client.get("/auth/me", headers={"X-API-Key": chave}).status_code == 401


def test_api_key_nao_e_guardada_em_claro(client, token_admin):
    from app.config import get_settings
    from app.users import repository

    chave = client.post("/auth/api-key", headers=auth(token_admin)).json()["api_key"]
    user = repository.get_user_by_username(get_settings().db_path, ADMIN)
    assert user.api_key_hash and chave not in user.api_key_hash
    assert user.api_key_prefix == chave[:12]


def test_api_key_desconhecida_recusada(client):
    assert client.get("/auth/me", headers={"X-API-Key": "nxp_inexistente"}).status_code == 401


# --- permissões -----------------------------------------------------------

def test_usuario_comum_nao_administra(client, token_admin):
    novo = {"username": "lecom", "password": "senha-forte-do-lecom", "rate_limit_per_hour": 5}
    assert client.post("/usuarios", json=novo, headers=auth(token_admin)).status_code == 201

    t = client.post("/auth/login", json={"username": "lecom",
                                         "password": novo["password"]}).json()["access_token"]
    assert client.get("/usuarios", headers=auth(t)).status_code == 403
    assert client.post("/ingest", json={}, headers=auth(t)).status_code == 403
    assert client.get("/auth/me", headers=auth(t)).status_code == 200


def test_senha_curta_recusada_ao_criar_usuario(client, token_admin):
    r = client.post("/usuarios", json={"username": "fraco", "password": "123"},
                    headers=auth(token_admin))
    assert r.status_code == 422


def test_usuario_inativo_perde_acesso(client, token_admin):
    client.post("/usuarios", json={"username": "desligado", "password": "senha-forte-123456"},
                headers=auth(token_admin))
    t = client.post("/auth/login", json={"username": "desligado",
                                         "password": "senha-forte-123456"}).json()["access_token"]
    assert client.get("/auth/me", headers=auth(t)).status_code == 200

    client.patch("/usuarios/desligado", json={"is_active": False}, headers=auth(token_admin))
    assert client.get("/auth/me", headers=auth(t)).status_code == 401
    assert client.post("/auth/login", json={"username": "desligado",
                                            "password": "senha-forte-123456"}).status_code == 401


# --- rate limit -----------------------------------------------------------

def _dubles(monkeypatch):
    """Substitui a geração de sugestão: aqui interessa só o que roda antes dela."""
    from app.rag import router as rag_router
    from app.rag.schemas import Classificacao, SugestaoResponse

    resposta = SugestaoResponse(
        classificacao=Classificacao(produto="NxPro (Plataforma Lecom)", tipo_chamado="Suporte",
                                    area_sugerida="N2 - Kaizen", criticidade_sugerida="Médio",
                                    confianca=0.5),
        mensagem_para_cliente="ok", resumo_para_n1="ok", recomendacao="abrir_chamado",
        chamados_similares=[], modelo="dublê", consulta="Assunto: teste",
    )
    monkeypatch.setattr(rag_router, "sugerir", lambda req: resposta)
    monkeypatch.setattr(rag_router, "get_collection",
                        lambda: type("C", (), {"count": lambda self: 1})())


def test_rate_limit_por_usuario(client, token_admin, monkeypatch):
    from app.rate_limit import rate_limiter

    rate_limiter.limpar()
    _dubles(monkeypatch)
    client.post("/usuarios", json={"username": "limitado", "password": "senha-forte-123456",
                                   "rate_limit_per_hour": 3}, headers=auth(token_admin))
    t = client.post("/auth/login", json={"username": "limitado",
                                         "password": "senha-forte-123456"}).json()["access_token"]

    codigos = [client.post("/sugerir", json={"assunto": "teste de limite"},
                           headers=auth(t)).status_code for _ in range(5)]
    assert codigos[:3] == [200, 200, 200], codigos
    assert codigos[3:] == [429, 429], codigos


def test_admin_nao_e_limitado(client, token_admin, monkeypatch):
    from app.rate_limit import rate_limiter

    rate_limiter.limpar()
    _dubles(monkeypatch)
    codigos = [client.post("/sugerir", json={"assunto": "teste admin"},
                           headers=auth(token_admin)).status_code for _ in range(8)]
    assert 429 not in codigos


def test_api_key_tambem_passa_pelo_rate_limit(client, token_admin, monkeypatch):
    from app.rate_limit import rate_limiter

    rate_limiter.limpar()
    _dubles(monkeypatch)
    client.post("/usuarios", json={"username": "integracao", "password": "senha-forte-123456",
                                   "rate_limit_per_hour": 2}, headers=auth(token_admin))
    chave = client.post("/usuarios/integracao/api-key",
                        headers=auth(token_admin)).json()["api_key"]

    codigos = [client.post("/sugerir", json={"assunto": "chamado via integracao"},
                           headers={"X-API-Key": chave}).status_code for _ in range(4)]
    assert codigos == [200, 200, 429, 429], codigos


def test_forca_bruta_no_login_bloqueia_ip(client):
    from app.rate_limit import login_limiter

    login_limiter.reset("testclient")
    for _ in range(10):
        client.post("/auth/login", json={"username": ADMIN, "password": "errada"})
    r = client.post("/auth/login", json={"username": ADMIN, "password": SENHA_ADMIN})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    login_limiter.reset("testclient")


# --- cabeçalhos e limites -------------------------------------------------

def test_cabecalhos_de_seguranca(client):
    h = client.get("/health").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in h
    assert h["Referrer-Policy"] == "no-referrer"


def test_corpo_grande_recusado(client, token_admin):
    grande = {"assunto": "teste", "descricao": "x" * (400 * 1024)}
    r = client.post("/sugerir", json=grande, headers=auth(token_admin))
    assert r.status_code == 413


def test_texto_acima_do_limite_do_schema(client, token_admin):
    r = client.post("/sugerir", json={"assunto": "t" * 600}, headers=auth(token_admin))
    assert r.status_code == 422


# --- validação de produção ------------------------------------------------

@pytest.mark.parametrize("mudanca,esperado", [
    ({"JWT_SECRET": "curto"}, "JWT_SECRET"),
    ({"ADMIN_PASSWORD": "curta"}, "ADMIN_PASSWORD"),
    ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS"),
    ({"ALLOWED_HOSTS": "*"}, "ALLOWED_HOSTS"),
])
def test_validacao_de_producao_reprova_configuracao_fraca(mudanca, esperado):
    from app.config import Settings

    base = {"jwt_secret": "x" * 48, "admin_password": "senha-de-teste-123456",
            "cors_origins": "https://bpm.nexum.com.br", "allowed_hosts": "n1.nexum.com.br"}
    base.update({k.lower(): v for k, v in mudanca.items()})
    problemas = Settings(**base).validar_producao()
    assert any(esperado in p for p in problemas), problemas


def test_configuracao_forte_passa_na_validacao():
    from app.config import Settings

    s = Settings(jwt_secret="x" * 48, admin_password="senha-de-teste-123456",
                 cors_origins="https://bpm.nexum.com.br", allowed_hosts="n1.nexum.com.br")
    assert s.validar_producao() == []
