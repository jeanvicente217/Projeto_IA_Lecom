"""Números de chamado são internos: nada que vai ao cliente pode citá-los."""
from __future__ import annotations

import pytest

from app.rag.schemas import ChamadoSimilar, Classificacao, SaidaModelo
from app.rag.service import _proteger_saida_cliente, remover_numeros_chamado

SIMILARES = [
    ChamadoSimilar(processo=14547, origem="lecom", assunto="a", similaridade=0.9),
    ChamadoSimilar(processo=8359, origem="historico", assunto="b", similaridade=0.8),
]


@pytest.mark.parametrize("entrada,esperado", [
    ("Reenvie o anexo em PDF (ref. chamado 14547).", "Reenvie o anexo em PDF."),
    ("Limpe o cache do navegador (ref. H8359)", "Limpe o cache do navegador"),
    ("Já existe o chamado 14547 em andamento sobre o assunto.",
     "Já existe o chamado em andamento sobre o assunto."),
    ("Veja os chamados 14547 e H8359, resolvidos assim.", "Veja os chamados, resolvidos assim."),
    ("Igual ao processo nº 14547.", "Igual ao processo."),
    ("Como no caso 14547, reinicie o robô.", "Como no caso, reinicie o robô."),
    ("Acompanhe pelo protocolo #20991.", "Acompanhe pelo protocolo."),
])
def test_remove_numeros(entrada, esperado):
    assert remover_numeros_chamado(entrada, SIMILARES) == esperado


def test_preserva_texto_sem_numero_e_quebras_de_linha():
    texto = "Antes de abrir:\n1. Reenvie o anexo.\n2. Confira o tamanho (até 10 MB)."
    assert remover_numeros_chamado(texto, SIMILARES) == texto


def test_saida_do_modelo_e_filtrada():
    saida = SaidaModelo(
        classificacao=Classificacao(produto="p", tipo_chamado="Suporte", area_sugerida="N2",
                                    criticidade_sugerida="Baixo", confianca=0.5),
        dicas=["Reenvie o anexo (ref. chamado 14547)", "(ref. H8359)"],
        mensagem_para_cliente="Já tivemos um caso parecido (chamado 14547). Reenvie o anexo.",
        resumo_para_n1="Precedente no chamado 14547.",
        recomendacao="tentar_dica_antes",
        justificativa="Similar 14547 e H8359.",
    )
    r = _proteger_saida_cliente(saida, SIMILARES)
    assert r.dicas == ["Reenvie o anexo"]
    assert "14547" not in r.mensagem_para_cliente
    # campos internos continuam citando os chamados para o N1
    assert "14547" in r.resumo_para_n1 and "H8359" in r.justificativa
