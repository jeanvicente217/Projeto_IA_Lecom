import math
from pathlib import Path

import pytest

from app.ingest.builder import BLOCO_ATENDIMENTO, montar_documento, texto_consulta
from app.ingest.loader import (
    detectar_formato,
    list_exports,
    load_chamados,
    load_historico,
    load_relatorio,
    read_export,
)

EXPORTS = Path(__file__).resolve().parent.parent / "data" / "exports"


def _por_formato(fmt: str) -> Path:
    for p in list_exports(EXPORTS):
        if detectar_formato(p) == fmt:
            return p
    pytest.skip(f"nenhum xlsx no formato '{fmt}' em data/exports")


@pytest.fixture(scope="module")
def relatorio_path():
    return _por_formato("relatorio")


@pytest.fixture(scope="module")
def historico_path():
    return _por_formato("historico")


@pytest.fixture(scope="module")
def relatorio(relatorio_path):
    return load_relatorio(relatorio_path)


@pytest.fixture(scope="module")
def historico(historico_path):
    return load_historico(historico_path)


# --- formato 1: relatório -------------------------------------------------

def test_read_export_encontra_cabecalho(relatorio_path):
    df = read_export(relatorio_path)
    assert "#Processo" in df.columns
    assert "Assunto:" in df.columns
    assert len(df) > 100


def test_load_relatorio_basico(relatorio):
    assert len(relatorio) > 100
    ids = [c.doc_id for c in relatorio]
    assert len(ids) == len(set(ids)), "processo duplicado no export"
    for c in relatorio:
        assert c.origem == "lecom"
        assert c.doc_id == str(c.processo)
        assert isinstance(c.processo, int)
        assert c.assunto
        for attr in ("produto", "tipo", "area", "cliente", "status"):
            v = getattr(c, attr)
            assert v is None or (isinstance(v, str) and v.lower() != "nan")


def test_cliente_e_cooperativa(relatorio):
    clientes = {c.cliente for c in relatorio if c.cliente}
    assert any("Sicoob" in x for x in clientes)


def test_relatorio_tem_resolucao_existe(relatorio):
    assert any(c.tem_resolucao for c in relatorio)


# --- formato 2: histórico do banco do formulário ------------------------------

def test_load_historico_basico(historico):
    assert len(historico) > 1000
    ids = [c.doc_id for c in historico]
    assert len(ids) == len(set(ids))
    for c in historico:
        assert c.origem == "historico"
        assert c.doc_id == f"h{c.processo}"
        assert c.assunto
        assert c.historico, "todo chamado do histórico deve ter pelo menos uma mensagem"
        assert c.status in {"Abertura", "Em andamento", "Avaliação", "Finalizado", "Reaberto", "Cancelado"}
        assert c.tipo != "Solicitação de melhoria"
        assert c.area is None or "  " not in c.area


def test_historico_dialogo(historico):
    com_analista = [c for c in historico if c.tem_resolucao]
    assert len(com_analista) > 1000
    c = com_analista[0]
    papeis = {p for p, _ in c.historico}
    assert "Analista" in papeis
    assert c.resposta_analista and len(c.resposta_analista) >= 15
    # lixo de teste não entra
    for c in historico:
        for _, t in c.historico:
            assert len(t) >= 15 and t.lower() not in {"teste", "qw"}


def test_historico_campos(historico):
    assert any(c.produto for c in historico)
    assert any(c.cliente and "Sicoob" in c.cliente for c in historico)
    assert any(c.abertura for c in historico)
    assert any(c.status == "Finalizado" for c in historico)


# --- detecção e ids -------------------------------------------------------------

def test_detectar_formato(relatorio_path, historico_path):
    assert detectar_formato(relatorio_path) == "relatorio"
    assert detectar_formato(historico_path) == "historico"
    assert load_chamados(relatorio_path)[0].origem == "lecom"


def test_ids_nao_colidem(relatorio, historico):
    assert not ({c.doc_id for c in relatorio} & {c.doc_id for c in historico})


# --- builder ---------------------------------------------------------------------

def test_documento_sem_nan(relatorio, historico):
    for c in relatorio + historico[:500]:
        d = montar_documento(c)
        assert d.id == c.doc_id
        assert d.texto.startswith("Assunto: ")
        assert "nan" not in d.texto.lower().split()
        for k, v in d.metadata.items():
            assert isinstance(v, (str, int, float, bool)), (k, type(v))
            if isinstance(v, float):
                assert not math.isnan(v)
        assert d.metadata["origem"] == c.origem
        assert len(d.metadata["hash"]) == 40


def test_documento_historico_tem_atendimento(historico):
    c = next(c for c in historico if c.tem_resolucao)
    d = montar_documento(c)
    assert BLOCO_ATENDIMENTO in d.texto
    assert "Analista: " in d.texto
    assert d.metadata["tem_resolucao"] is True


def test_hash_estavel(relatorio):
    c = relatorio[0]
    assert montar_documento(c).hash == montar_documento(c).hash


def test_texto_consulta():
    t = texto_consulta("Erro ao abrir subprocesso", "  não abre no celular ")
    assert t == "Assunto: Erro ao abrir subprocesso\nDescrição do cliente:\nnão abre no celular"
    assert texto_consulta("X", "") == "Assunto: X"
