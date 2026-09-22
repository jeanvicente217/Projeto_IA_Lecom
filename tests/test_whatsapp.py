"""Testes do bot de WhatsApp: normalização, autorização, webhook e vazamento de dados.

Usa uma pasta data/raw sintética, para controlar o conteúdo e provar que campos internos
nunca saem na resposta. O envio pela Evolution é capturado por um dublê.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

ADMIN, SENHA = "admin", "senha-de-teste-123456"
TOKEN_WEBHOOK = "t" * 32
EVO_KEY = "chave-evolution-teste"

TEL_JEAN = "14991449422"          # cadastrado, cooperativa Nexum, escopo proprio
TEL_GESTOR = "11988887777"        # cadastrado, cooperativa Nexum, escopo cooperativa
TEL_OUTRO = "21977776666"         # cadastrado em OUTRA cooperativa
TEL_DESCONHECIDO = "31966665555"  # não cadastrado
TEL_AUTO = "17999998888"          # cadastrado, cooperativa Nexum, escopo proprio, sem chamados próprios

NOTA_INTERNA = "SEGREDO-INTERNO escalado para o squad X"
EMAIL_INTERNO = "analista@nexum.com.br"


def _chamado(n, coop, nome, tel, status="Aprovado", resposta="Resolvido: reinicie o robô."):
    return {
        "processo": n, "raspado_em": "2026-09-21T15:00:00",
        "cabecalho": f"Abertura de Chamados {n}{status} - aberto por {nome} 18 de set de 2026",
        "atividades": [{"atividade": "Executar atendimento", "status_texto": status,
                        "responsavel": "Analista Interno"}],
        "detalhes": {"processStartDate": "2026-09-18T16:08:38.000-03:00"},
        "campos": {"LT_ASSUNTO": f"Assunto do chamado {n}", "LT_COOPERATIVA2": coop,
                   "LT_NOME": nome, "LT_TELEFONE_SOLICITANTE": tel,
                   "LST_TIPO_PRODUTO": "NxBot (RPA)", "AUX_EMAIL_NEXUM": EMAIL_INTERNO,
                   "CT_OBS_SQUAD": NOTA_INTERNA},
        "historico": [
            {"campo": "CT_OBS_SQUAD", "itens": [{"responsavel": "N1", "data": "2026-09-19T10:00:00",
                                                 "atividade": "Executar atendimento",
                                                 "texto": NOTA_INTERNA}]},
            {"campo": "CT_DETALHAMENTO_CHAMADO", "itens": [
                {"responsavel": "Analista Interno", "data": "2026-09-19T11:00:00",
                 "atividade": "Executar atendimento", "texto": resposta}]},
        ],
    }


@pytest.fixture(scope="module")
def ambiente(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wa")
    raw = tmp / "raw"
    raw.mkdir()
    chamados = [
        _chamado(14052, "Nexum", "Gabriel Andrade", "(14) 99144-9422"),          # do Jean
        _chamado(14100, "Nexum", "Outra Pessoa", "(11) 91111-2222"),             # Nexum, de outro
        _chamado(14200, "Sicoob Credimogiana", "Fulano", "(21) 97777-6666"),     # outra coop
        _chamado(14300, "Nexum", "Gabriel Andrade", "1491449422", status="Em andamento",
                 resposta=""),                                                   # sem o nono dígito
        _chamado(14400, "Nexum", "Gabriel Andrade", "(14) 99144-9422",
                 resposta="Ajustamos a configuração, deve funcionar agora."),     # p/ satisfação "sim"
        _chamado(14500, "Nexum", "Gabriel Andrade", "(14) 99144-9422",
                 resposta="Verifique se o cabo de rede está bem conectado."),     # p/ satisfação "não"
        _chamado(14600, "Nexum", "Gabriel Andrade", "(14) 99144-9422",
                 resposta="Já está funcionando, pode confirmar?"),                # p/ testar "pergunta só uma vez"
    ]
    for c in chamados:
        (raw / f"{c['processo']}.json").write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")

    os.environ.update({
        "DATABASE_PATH": str(tmp / "users.db"),
        "CHROMA_PATH": str(tmp / "chroma"),
        "JWT_SECRET": "x" * 48,
        "ADMIN_USERNAME": ADMIN, "ADMIN_PASSWORD": SENHA,
        "OPENAI_API_KEY": "fake", "PRODUCAO": "false",
        "CORS_ORIGINS": "https://bpm.nexum.com.br", "ALLOWED_HOSTS": "*",
        "WHATSAPP_HABILITADO": "true",
        "WHATSAPP_WEBHOOK_TOKEN": TOKEN_WEBHOOK,
        "EVOLUTION_URL": "https://evolution.teste", "EVOLUTION_INSTANCE": "nexum",
        "EVOLUTION_API_KEY": EVO_KEY,
        "WHATSAPP_RAW_PATH": str(raw),
        "WHATSAPP_MSGS_POR_10MIN": "4",
        "WHATSAPP_MAX_LISTA": "20",  # os testes têm vários chamados sintéticos na mesma cooperativa
        "WHATSAPP_RESUMO_IA": "false",
    })
    from app import config
    config.get_settings.cache_clear()
    import app.main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c, tmp
    config.get_settings.cache_clear()


@pytest.fixture(scope="module")
def client(ambiente):
    return ambiente[0]


@pytest.fixture(scope="module")
def admin(client):
    tok = client.post("/auth/login", json={"username": ADMIN, "password": SENHA}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for tel, nome, coop, escopo in [
        (TEL_JEAN, "Gabriel Andrade", "Nexum", "proprio"),
        (TEL_GESTOR, "Gestora Nexum", "Nexum", "cooperativa"),
        (TEL_OUTRO, "Fulano", "Sicoob Credimogiana", "proprio"),
        (TEL_AUTO, "Cliente Auto", "Nexum", "proprio"),
    ]:
        r = client.post("/whatsapp/contatos", headers=h,
                        json={"telefone": tel, "nome": nome, "cooperativa": coop, "escopo": escopo})
        assert r.status_code == 201, r.text
    return h


@pytest.fixture(autouse=True)
def limitador_zerado():
    """Cada teste começa sem histórico de mensagens, senão um bloqueia o seguinte."""
    from app.whatsapp.router import limite_por_telefone
    limite_por_telefone.limpar()


@pytest.fixture
def enviados(monkeypatch):
    """Captura o que seria enviado pela Evolution."""
    from app.whatsapp import evolution
    caixa: list[tuple[str, str]] = []
    monkeypatch.setattr(evolution.EvolutionClient, "enviar_texto",
                        lambda self, tel, txt: caixa.append((tel, txt)) or True)
    return caixa


def _payload(tel: str, texto: str, msg_id: str, from_me=False, grupo=False, apikey=EVO_KEY):
    jid = f"{tel}@g.us" if grupo else f"55{tel}@s.whatsapp.net"
    return {"event": "messages.upsert", "instance": "nexum", "apikey": apikey,
            "data": {"key": {"remoteJid": jid, "fromMe": from_me, "id": msg_id},
                     "message": {"conversation": texto}, "messageType": "conversation"}}


def _webhook(client, tel, texto, msg_id, **kw):
    return client.post(f"/whatsapp/webhook/{TOKEN_WEBHOOK}", json=_payload(tel, texto, msg_id, **kw))


# --- telefone ---------------------------------------------------------------

def test_normalizacao_de_telefone():
    from app.whatsapp.telefone import mesmo_numero, normalizar, variantes

    assert normalizar("(14) 99144-9422") == "5514991449422"
    assert normalizar("14991449422") == "5514991449422"
    assert normalizar("5514991449422@s.whatsapp.net") == "5514991449422"
    assert normalizar("1491449422") == "551491449422"
    assert normalizar("1234") is None and normalizar("") is None and normalizar(None) is None
    assert "551491449422" in variantes("5514991449422"), "sem o nono dígito é o mesmo número"
    assert mesmo_numero("(14) 99144-9422", "1491449422")
    assert not mesmo_numero("14991449422", "14991449423")


# --- autorização (regra pura) ---------------------------------------------------

def test_regras_de_autorizacao():
    from app.whatsapp.consulta import autorizado
    from app.whatsapp.repositorio import Contato

    jean = Contato("5514991449422", "Gabriel Andrade", "Nexum", "proprio", True)
    gestor = Contato("5511988887777", "Gestora", "Nexum", "cooperativa", True)
    inativo = Contato("5511988887777", "Gestora", "Nexum", "cooperativa", False)
    do_jean = {"cooperativa": "Nexum", "telefone": "5514991449422", "solicitante": "Gabriel Andrade"}
    de_outro = {"cooperativa": "Nexum", "telefone": "5511911112222", "solicitante": "Outra Pessoa"}
    outra_coop = {"cooperativa": "Sicoob Credimogiana", "telefone": "5514991449422", "solicitante": "Gabriel Andrade"}

    assert autorizado(jean, do_jean)
    assert not autorizado(jean, de_outro), "escopo próprio não vê chamado de colega"
    assert not autorizado(jean, outra_coop), "mesmo telefone, cooperativa diferente: nega"
    assert autorizado(gestor, do_jean) and autorizado(gestor, de_outro), "escopo cooperativa vê todos da empresa"
    assert not autorizado(gestor, outra_coop)
    assert not autorizado(inativo, do_jean)


def test_interpretacao_de_comandos():
    from app.whatsapp.consulta import interpretar

    assert interpretar("14764") == ("chamado", 14764)
    assert interpretar("chamado 14764 por favor") == ("chamado", 14764)
    assert interpretar("meus chamados") == ("lista", None)
    assert interpretar("oi") == ("ajuda", None)
    assert interpretar("") == ("ajuda", None)


# --- webhook: proteções -----------------------------------------------------------

def test_webhook_token_errado_da_404(client, enviados):
    r = client.post("/whatsapp/webhook/token-errado", json=_payload(TEL_JEAN, "14052", "m1"))
    assert r.status_code == 404 and enviados == []


def test_webhook_apikey_errada_da_401(client, enviados):
    r = _webhook(client, TEL_JEAN, "14052", "m2", apikey="outra")
    assert r.status_code == 401 and enviados == []


def test_webhook_ignora_grupo_e_proprias_mensagens(client, admin, enviados):
    assert _webhook(client, TEL_JEAN, "14052", "m3", grupo=True).json()["ignorado"]
    assert _webhook(client, TEL_JEAN, "14052", "m4", from_me=True).json()["ignorado"]
    assert enviados == []


def test_webhook_deduplica_reenvio(client, admin, enviados):
    _webhook(client, TEL_GESTOR, "ajuda", "repetida")
    r = _webhook(client, TEL_GESTOR, "ajuda", "repetida")
    assert r.json().get("motivo") == "duplicado"
    assert len(enviados) == 1


# --- webhook: dados e vazamento ------------------------------------------------------

def test_nao_cadastrado_nao_recebe_nada(client, admin, enviados):
    """Número fora da lista de permissão: silêncio total, nem mensagem genérica."""
    r = _webhook(client, TEL_DESCONHECIDO, "14052", "m5")
    assert r.status_code == 200
    assert r.json().get("motivo") == "nao_cadastrado"
    assert enviados == [], "número não cadastrado não deve receber resposta alguma"


def test_cadastrado_ve_o_proprio_chamado_sem_campos_internos(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14052", "m6")
    tel, txt = enviados[-1]
    assert "*Chamado 14052*" in txt
    assert "Assunto do chamado 14052" in txt
    assert "Aprovado" in txt
    assert "Resolvido: reinicie o robô." in txt, "a resposta do analista é visível ao cliente"
    assert NOTA_INTERNA not in txt, "observação interna NÃO pode aparecer"
    assert EMAIL_INTERNO not in txt
    assert "Analista Interno" not in txt, "nome do responsável interno não aparece"


def test_chamado_sem_nono_digito_no_cadastro_ainda_e_do_dono(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14300", "m7")
    assert "*Chamado 14300*" in enviados[-1][1]


def test_chamado_de_outra_pessoa_e_de_outra_cooperativa_dao_a_mesma_resposta(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14100", "m8")   # mesma cooperativa, outro solicitante
    _webhook(client, TEL_JEAN, "14200", "m9")   # outra cooperativa
    _webhook(client, TEL_JEAN, "99999", "m10")  # não existe
    respostas = [t for _, t in enviados[-3:]]
    assert all("Não encontrei o chamado" in r for r in respostas)
    assert all("Assunto do chamado" not in r for r in respostas)
    # mesma frase, só muda o número: impossível distinguir "existe mas não é seu" de "não existe"
    modelo = [r.replace("14100", "N").replace("14200", "N").replace("99999", "N") for r in respostas]
    assert len(set(modelo)) == 1


def test_escopo_cooperativa_ve_todos_da_empresa_mas_nao_de_outra(client, admin, enviados):
    _webhook(client, TEL_GESTOR, "14100", "m11")
    assert "*Chamado 14100*" in enviados[-1][1]
    _webhook(client, TEL_GESTOR, "14200", "m12")
    assert "Não encontrei" in enviados[-1][1]


def test_lista_respeita_escopo(client, admin, enviados):
    _webhook(client, TEL_JEAN, "meus chamados", "m13")
    lista_jean = enviados[-1][1]
    assert "14052" in lista_jean and "14300" in lista_jean
    assert "14100" not in lista_jean and "14200" not in lista_jean

    _webhook(client, TEL_GESTOR, "meus chamados", "m14")
    lista_gestor = enviados[-1][1]
    assert "14052" in lista_gestor and "14100" in lista_gestor
    assert "14200" not in lista_gestor


def test_limite_de_mensagens_por_telefone(client, admin, enviados):
    from app.whatsapp.router import limite_por_telefone
    limite_por_telefone.limpar()
    for i in range(6):
        _webhook(client, TEL_OUTRO, "ajuda", f"lim{i}")
    textos = [t for tel, t in enviados if tel == "55" + TEL_OUTRO]
    assert sum("muitas consultas" in t for t in textos) >= 2, "a partir da 5ª mensagem bloqueia"


# --- autoatendimento: descrição livre roda a mesma inteligência do /sugerir ------------

def _resp_sugestao(recomendacao: str, mensagem: str):
    from app.rag.schemas import Classificacao, SugestaoResponse

    return SugestaoResponse(
        classificacao=Classificacao(produto="NxBot (RPA)", tipo_chamado="Suporte",
                                    area_sugerida="N3 - RPA", criticidade_sugerida="Alto",
                                    confianca=0.8),
        dicas=["dica de teste"], mensagem_para_cliente=mensagem, resumo_para_n1="resumo",
        recomendacao=recomendacao, justificativa="teste",
        chamados_similares=[], modelo="dublê", consulta="Assunto: teste",
    )


def test_interpretacao_reconhece_descricao_e_numero_no_meio_da_frase():
    from app.whatsapp.consulta import interpretar

    assert interpretar("qual o status do chamado 14764?") == ("chamado", 14764)
    assert interpretar("meu robô parou de funcionar hoje de manhã") == ("descricao", None)
    assert interpretar("já tentei reiniciar e continua dando erro na tela") == ("descricao", None)
    assert interpretar("ok") == ("ajuda", None)


def test_autoatendimento_dica_nao_oferece_abertura(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    monkeypatch.setattr(rag_service, "sugerir", lambda req: _resp_sugestao(
        "tentar_dica_antes", "Tente reiniciar o robô. (ref. chamado 999)"))
    _webhook(client, TEL_AUTO, "meu robo nao esta funcionando desde ontem de manha", "auto1")
    txt = enviados[-1][1]
    assert "Tente reiniciar o robô" in txt
    assert "me chamar de novo" in txt
    assert "Responda *sim* ou *não*" not in txt


def test_autoatendimento_abrir_pede_confirmacao_e_sim_registra_pre_chamado(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    monkeypatch.setattr(rag_service, "sugerir", lambda req: _resp_sugestao(
        "abrir_chamado", "Não encontrei um caso igual. Recomendo abrir o chamado."))
    _webhook(client, TEL_AUTO, "o sistema trava toda vez que eu tento salvar um cadastro novo", "auto2")
    assert "Responda *sim* ou *não*" in enviados[-1][1]

    _webhook(client, TEL_AUTO, "sim", "auto3")
    confirmado = enviados[-1][1]
    assert "Registrado!" in confirmado and "protocolo interno #" in confirmado

    itens = client.get("/whatsapp/pre-chamados", headers=admin).json()
    meu = next(p for p in itens if p["telefone"] == "55" + TEL_AUTO and p["origem"] == "sugestao")
    assert meu["status"] == "pendente"
    assert "trava" in meu["descricao"]
    assert meu["classificacao"]["produto"] == "NxBot (RPA)"


def test_autoatendimento_nao_confirma_nao_registra_nada(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    monkeypatch.setattr(rag_service, "sugerir", lambda req: _resp_sugestao("abrir_chamado", "Recomendo abrir."))
    antes = len(client.get("/whatsapp/pre-chamados", headers=admin).json())
    _webhook(client, TEL_AUTO, "quero descrever mais um problema diferente por aqui agora", "auto4")
    _webhook(client, TEL_AUTO, "não, deixa pra lá", "auto5")
    assert "não vou registrar" in enviados[-1][1]
    depois = len(client.get("/whatsapp/pre-chamados", headers=admin).json())
    assert depois == antes


def test_confirmacao_ambigua_cai_para_novo_pedido_em_vez_de_travar(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    monkeypatch.setattr(rag_service, "sugerir", lambda req: _resp_sugestao("abrir_chamado", "Recomendo abrir."))
    _webhook(client, TEL_AUTO, "mais uma descricao de problema aqui so para testar o fluxo", "auto6")
    # em vez de sim/não, manda um número de chamado: não pode ficar preso na pergunta anterior
    _webhook(client, TEL_AUTO, "14052", "auto7")
    assert "Não encontrei o chamado" in enviados[-1][1], "14052 não é do TEL_AUTO"


def test_numero_nao_autorizado_embutido_em_frase_vira_descricao(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    monkeypatch.setattr(rag_service, "sugerir", lambda req: _resp_sugestao("tentar_dica_antes", "Dica de teste aqui."))
    _webhook(client, TEL_AUTO, "acho que é parecido com o chamado 14100 que nunca teve resposta", "auto8")
    assert "Dica de teste aqui." in enviados[-1][1]


def test_falha_no_rag_ainda_assim_registra_pre_chamado(client, admin, enviados, monkeypatch):
    from app.rag import service as rag_service

    def _quebra(req):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(rag_service, "sugerir", _quebra)
    antes = len(client.get("/whatsapp/pre-chamados", headers=admin).json())
    _webhook(client, TEL_AUTO, "mensagem descrevendo um problema generico sem numero nenhum", "auto9")
    assert "protocolo interno #" in enviados[-1][1]
    depois = len(client.get("/whatsapp/pre-chamados", headers=admin).json())
    assert depois == antes + 1


# --- satisfação: pergunta uma vez, e "não" oferece reabertura --------------------------

def test_pergunta_satisfacao_apos_resposta_do_atendimento_uma_unica_vez(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14600", "sat1")
    assert "Isso resolveu o que você precisava?" in enviados[-1][1]

    _webhook(client, TEL_JEAN, "14600", "sat2")
    assert "*Chamado 14600*" in enviados[-1][1]
    assert "Isso resolveu o que você precisava?" not in enviados[-1][1], "só pergunta uma vez"


def test_satisfacao_sim(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14400", "sat3")
    assert "Isso resolveu o que você precisava?" in enviados[-1][1]
    _webhook(client, TEL_JEAN, "sim", "sat4")
    assert "fico feliz em ajudar" in enviados[-1][1].lower()


def test_satisfacao_nao_oferece_reabertura_e_confirmacao_registra(client, admin, enviados):
    _webhook(client, TEL_JEAN, "14500", "sat5")
    assert "Isso resolveu o que você precisava?" in enviados[-1][1]

    _webhook(client, TEL_JEAN, "não", "sat6")
    oferta = enviados[-1][1]
    assert "reabr" in oferta.lower() or "retomar" in oferta.lower()
    assert "Responda *sim* ou *não*" in oferta

    antes = len(client.get("/whatsapp/pre-chamados", headers=admin).json())
    _webhook(client, TEL_JEAN, "sim", "sat7")
    assert "Registrado!" in enviados[-1][1]
    depois = client.get("/whatsapp/pre-chamados", headers=admin).json()
    assert len(depois) == antes + 1
    novo = max(depois, key=lambda p: p["id"])
    assert novo["origem"] == "reabertura"


# --- imagem/anexo: reconhece e confirma, sem vazar dado para não cadastrado ------------

def _payload_imagem(tel: str, msg_id: str) -> dict:
    return {"event": "messages.upsert", "instance": "nexum", "apikey": EVO_KEY,
            "data": {"key": {"remoteJid": f"55{tel}@s.whatsapp.net", "fromMe": False, "id": msg_id},
                     "message": {"imageMessage": {"url": "enc://fake", "mimetype": "image/jpeg"}},
                     "messageType": "imageMessage"}}


def test_imagem_de_cadastrado_recebe_confirmacao(client, admin, enviados):
    r = client.post(f"/whatsapp/webhook/{TOKEN_WEBHOOK}", json=_payload_imagem(TEL_AUTO, "img1"))
    assert r.status_code == 200 and r.json().get("tipo") == "anexo"
    assert "Recebi seu arquivo" in enviados[-1][1]


def test_imagem_de_nao_cadastrado_e_silenciosa(client, admin, enviados):
    r = client.post(f"/whatsapp/webhook/{TOKEN_WEBHOOK}", json=_payload_imagem(TEL_DESCONHECIDO, "img2"))
    assert r.status_code == 200 and r.json().get("motivo") == "nao_cadastrado"
    assert enviados == []


# --- pré-chamados: administração ------------------------------------------------------

def test_admin_lista_e_atualiza_pre_chamados(client, admin):
    assert client.get("/whatsapp/pre-chamados").status_code == 401
    itens = client.get("/whatsapp/pre-chamados", headers=admin).json()
    assert len(itens) >= 1
    algum = itens[0]

    r = client.patch(f"/whatsapp/pre-chamados/{algum['id']}", headers=admin,
                     json={"status": "aberto", "numero_chamado": "99999"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "aberto" and r.json()["numero_chamado"] == "99999"

    assert client.patch("/whatsapp/pre-chamados/999999", headers=admin,
                        json={"status": "descartado"}).status_code == 404
    assert client.get("/whatsapp/pre-chamados?status_filtro=aberto", headers=admin).json()


# --- notificações proativas: só na mudança, nunca no primeiro sinal --------------------

def test_notificacoes_primeira_vez_nao_avisa_depois_muda_e_avisa(client, admin, monkeypatch):
    from pathlib import Path

    from app.config import get_settings
    from app.whatsapp import indice, notificacoes

    enviados_notif: list[tuple[str, str]] = []
    monkeypatch.setattr(notificacoes.EvolutionClient, "enviar_texto",
                        lambda self, tel, txt: enviados_notif.append((tel, txt)) or True)

    settings = get_settings()
    caminho_idx = Path(settings.db_path).parent / "chamados_idx.db"

    r1 = notificacoes.verificar_e_notificar(settings)
    assert r1["notificados"] == 0, "primeira checagem só grava o hash, não notifica nada"
    assert r1["primeira_vez"] > 0

    arq = settings.whatsapp_raw_dir / "14052.json"
    dados = json.loads(arq.read_text(encoding="utf-8"))
    dados["cabecalho"] = dados["cabecalho"].replace("Aprovado", "Em alerta")
    dados["raspado_em"] = "2026-09-22T18:00:00"
    arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    indice.atualizar(settings.whatsapp_raw_dir, caminho_idx, forcar=True)

    r2 = notificacoes.verificar_e_notificar(settings)
    assert r2["notificados"] >= 1
    assert any("14052" in t for _, t in enviados_notif)

    r3 = notificacoes.verificar_e_notificar(settings)
    assert r3["notificados"] == 0, "não notifica de novo sem mudança nova"


def test_resumo_semanal_envia_so_para_escopo_cooperativa(client, admin, monkeypatch):
    from app.config import get_settings
    from app.whatsapp import notificacoes

    enviados_resumo: list[tuple[str, str]] = []
    monkeypatch.setattr(notificacoes.EvolutionClient, "enviar_texto",
                        lambda self, tel, txt: enviados_resumo.append((tel, txt)) or True)

    r = notificacoes.enviar_resumo_semanal(get_settings())
    assert r["enviados"] >= 1
    assert all(tel == "55" + TEL_GESTOR for tel, _ in enviados_resumo), \
        "só contatos com escopo cooperativa recebem o resumo"
    _, texto = enviados_resumo[-1]
    assert "Resumo semanal" in texto and "Nexum" in texto


def test_endpoints_notificar_e_resumo_semanal(client, admin, monkeypatch):
    from app.whatsapp import notificacoes

    monkeypatch.setattr(notificacoes.EvolutionClient, "enviar_texto", lambda self, tel, txt: True)
    assert client.post("/whatsapp/notificar").status_code == 401
    assert client.post("/whatsapp/resumo-semanal").status_code == 401

    r = client.post("/whatsapp/notificar", headers=admin)
    assert r.status_code == 200 and "verificados" in r.json()
    r = client.post("/whatsapp/resumo-semanal", headers=admin)
    assert r.status_code == 200 and "enviados" in r.json()


# --- admin ---------------------------------------------------------------------

def test_simular_exige_admin_e_nao_envia(client, admin, enviados):
    assert client.post("/whatsapp/simular", json={"telefone": TEL_JEAN, "texto": "14052"}).status_code == 401
    r = client.post("/whatsapp/simular", headers=admin, json={"telefone": TEL_JEAN, "texto": "14052"})
    assert r.status_code == 200, r.text
    assert "*Chamado 14052*" in r.json()["resposta"]
    assert r.json()["comando"] == "chamado"
    assert enviados == [], "simular não dispara envio"


def test_simular_numero_nao_cadastrado_devolve_resposta_nula(client, admin, enviados):
    """A simulação reflete o mesmo silêncio do webhook real para número não cadastrado."""
    r = client.post("/whatsapp/simular", headers=admin,
                    json={"telefone": TEL_DESCONHECIDO, "texto": "14052"})
    assert r.status_code == 200, r.text
    assert r.json()["resposta"] is None
    assert enviados == []


def test_contatos_crud_e_status(client, admin):
    r = client.get("/whatsapp/contatos", headers=admin)
    assert {c["telefone"] for c in r.json()} >= {"55" + TEL_JEAN, "55" + TEL_GESTOR}
    assert client.post("/whatsapp/contatos", headers=admin,
                       json={"telefone": "123", "nome": "x", "cooperativa": "y"}).status_code == 422
    st = client.get("/whatsapp/status", headers=admin).json()
    assert st["habilitado"] and st["evolution_configurada"] and st["chamados_indexados"] == 7
    assert client.delete(f"/whatsapp/contatos/{TEL_OUTRO}", headers=admin).status_code == 204
    assert client.delete(f"/whatsapp/contatos/{TEL_OUTRO}", headers=admin).status_code == 404


def test_webhook_desligado_da_404(client, monkeypatch):
    from app import config
    s = config.get_settings()
    monkeypatch.setattr(s, "whatsapp_habilitado", False)
    assert _webhook(client, TEL_JEAN, "14052", "off").status_code == 404
