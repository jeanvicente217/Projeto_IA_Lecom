"""Testes do conversor da raspagem (data/raw -> Chamado). Não usam navegador nem rede."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.builder import BLOCO_ATENDIMENTO, montar_documento
from app.ingest.raspagem import chamado_de_json, load_raspagem

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

EXEMPLO = {
    "processo": 11643,
    "raspado_em": "2026-09-21T11:12:00",
    "cabecalho": "Abertura de Chamados 11.643Aprovado - aberto por Giorgia Alexia Campos Nunes 26 de fev de 2026 às 15:13",
    "detalhes": {"processStartDate": "2026-02-26T15:13:40.000-03:00",
                 "activityTitle": "Finalização Automática"},
    "campos": {
        "LT_ASSUNTO": "Processo Crédito Manutenção v.4",
        "LST_TIPO_PRODUTO": "NxPro (Plataforma Lecom)",
        "LST_PRODUTO": "Informe de erro",
        "LST_RESP": "N2  -  Phoenix",
        "LST_NIVEL_CRITICIDADE": "Alto",
        "LT_COOPERATIVA2": "Sicoob Sul",
        "LT_NOME": "Giorgia Alexia Campos Nunes",
        "RB_AMBIENTE": "Aceite",
        "RB_SATISFACAO": "Ótimo",
    },
    "historico": [
        {"campo": "CT_OBS_SQUAD", "itens": [
            {"responsavel": "José Segura", "data": "2026-02-27T11:05:00.000-03:00",
             "atividade": "Executar  atendimento",
             "texto": "Escalando chamado para Poli conforme alinhado via Teams."}]},
        {"campo": "CT_DETALHAMENTO_CHAMADO", "itens": [
            {"responsavel": "Guilherme Poli", "data": "2026-02-27T13:02:00.000-03:00",
             "atividade": "Executar  atendimento",
             "texto": "Oi, bom dia! O processo segue o fluxo como esperado."}]},
        {"campo": "CT_DECREVA_SOLICITACAO", "itens": [
            {"responsavel": "Giorgia Alexia Campos Nunes", "data": "2026-02-26T15:18:00.000-03:00",
             "atividade": "Abertura",
             "texto": "Boa tarde! Na etapa de aprovação dá erro de (No user Group0)."}]},
    ],
    "anexos": [],
}


def test_converte_exemplo():
    c = chamado_de_json(EXEMPLO)
    assert c is not None
    assert c.processo == 11643
    assert c.origem == "lecom" and c.doc_id == "11643"
    assert c.assunto == "Processo Crédito Manutenção v.4"
    assert c.produto == "NxPro (Plataforma Lecom)"
    assert c.tipo == "Informe de erro"
    assert c.area == "N2 - Phoenix", "espaços duplicados devem ser normalizados"
    assert c.cliente == "Sicoob Sul"
    assert c.status == "Aprovado"
    assert c.abertura and c.abertura.year == 2026 and c.abertura.month == 2


def test_dialogo_em_ordem_cronologica():
    c = chamado_de_json(EXEMPLO)
    papeis = [p for p, _ in c.historico]
    # abertura (26/02) vem antes da observação interna e da resposta (27/02)
    assert papeis[0] == "Cliente"
    assert papeis == ["Cliente", "N1 (interno)", "Analista"]
    assert c.tem_resolucao
    assert "fluxo como esperado" in c.resposta_analista
    assert "No user Group0" in c.descricao_cliente


def test_papel_por_autor():
    """Quem escreve com o nome do solicitante é o cliente, mesmo fora da abertura."""
    dados = json.loads(json.dumps(EXEMPLO))
    dados["historico"].append({"campo": "CT_DETALHAMENTO_CHAMADO", "itens": [
        {"responsavel": "Giorgia Alexia Campos Nunes", "data": "2026-02-28T09:00:00.000-03:00",
         "atividade": "Analisar  atendimento", "texto": "Obrigada, validei aqui e está certo!"}]})
    c = chamado_de_json(dados)
    assert c.historico[-1][0] == "Cliente"
    assert "validei aqui" in c.historico[-1][1]


def test_pulados_e_erros_viram_none():
    assert chamado_de_json({"processo": 1, "pulado": "sem atividade aprovada"}) is None
    assert chamado_de_json({"processo": 1, "erro": "falhou"}) is None


def test_sem_conteudo_vira_none():
    assert chamado_de_json({"processo": 1, "campos": {}, "historico": []}) is None


def test_assunto_derivado_da_descricao():
    dados = json.loads(json.dumps(EXEMPLO))
    del dados["campos"]["LT_ASSUNTO"]
    c = chamado_de_json(dados)
    assert c.assunto.startswith("Boa tarde")


def test_documento_do_chamado_raspado():
    d = montar_documento(chamado_de_json(EXEMPLO))
    assert d.id == "11643"
    assert d.texto.startswith("Assunto: Processo Crédito Manutenção v.4")
    assert BLOCO_ATENDIMENTO in d.texto
    assert "Analista: " in d.texto and "Cliente: " in d.texto
    assert d.metadata["tem_resolucao"] is True
    assert d.metadata["origem"] == "lecom"


@pytest.mark.skipif(not list(RAW.glob("*.json")), reason="sem raspagem em data/raw")
def test_carrega_raspagem_real():
    chamados = load_raspagem(RAW)
    assert chamados, "nenhum chamado aproveitável na raspagem"
    for c in chamados:
        assert c.origem == "lecom"
        assert c.assunto and isinstance(c.processo, int)
        for papel, texto in c.historico:
            assert texto.strip() and len(texto) >= 15
            assert papel in {"Cliente", "Analista", "N1", "N1 (interno)",
                             "Cliente (avaliação)", "Gestor"}
    # todo chamado aproveitado tem ao menos a mensagem do cliente
    assert all(c.historico or c.descricao_cliente for c in chamados)
    # a resposta do analista não existe em chamados ainda em aberto, então o piso é baixo
    com_resposta = [c for c in chamados if c.tem_resolucao]
    assert len(com_resposta) >= len(chamados) * 0.3, (
        f"só {len(com_resposta)}/{len(chamados)} com resposta do analista")
