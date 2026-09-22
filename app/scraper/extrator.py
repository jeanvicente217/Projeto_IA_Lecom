"""Extração dos dados de um chamado.

A tela do formulário dispara APIs internas que já devolvem o que interessa, estruturado.
Em vez de raspar o DOM (frágil e dependente da renderização), capturamos essas respostas:

  /form-web/api/form/process-instances    -> metadados (título, datas, responsável)
  /form-web/api/form/form-initial-values  -> valor de cada campo do formulário
  /form-app/.../business-data/history     -> histórico por campo, com autor, data e etapa
  /form-app/.../data-grid?gridId=ANEXO    -> anexos

O DOM é a reserva, usada quando alguma dessas respostas não chega.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from app.scraper.lecom import (
    BASE,
    cabecalho_chamado,
    escolher_atividade,
    listar_atividades,
)

log = logging.getLogger(__name__)

# fragmento da URL -> chave onde o corpo é guardado
APIS = {
    "/form/process-instances": "detalhes",
    "/form/form-initial-values": "valores",
    "business-data/history": "historico",
    "data-grid": "grid",
}
API_ESSENCIAL = ("valores", "historico")

JS_DOM_FALLBACK = """
() => {
  const campos = {};
  for (const el of document.querySelectorAll('[name][id]')) {
    const v = el.getAttribute('rawvalue') ?? el.value;
    if (v != null && v !== '' && !(el.name in campos)) campos[el.name] = v;
  }
  const historico = [];
  for (const h of document.querySelectorAll('div.history[id^="history__field-"]')) {
    const itens = [...h.querySelectorAll('.history__item')].map(it => ({
      cabecalho: (it.querySelector('.history__item-header') || {}).innerText || '',
      texto: (it.querySelector('.history__description') || {}).innerText || '',
    })).filter(i => i.texto.trim());
    if (itens.length) historico.push({campo: h.id.replace('history__field-', ''), itens});
  }
  return {campos, historico};
}
"""


class ColetorApi:
    """Guarda os corpos JSON das APIs de interesse disparadas pela página."""

    def __init__(self) -> None:
        self.corpos: dict[str, Any] = {}

    def limpar(self) -> None:
        self.corpos.clear()

    async def on_response(self, resp) -> None:
        chave = next((c for frag, c in APIS.items() if frag in resp.url), None)
        if chave is None:
            return
        try:
            self.corpos[chave] = json.loads(await resp.text())
        except Exception:  # noqa: BLE001  (resposta vazia, cancelada ou não-JSON)
            pass

    def completo(self) -> bool:
        return all(k in self.corpos for k in API_ESSENCIAL)


def _campos_de(valores: dict | None) -> dict[str, str]:
    """{'LT_ASSUNTO': 'Erro X', ...} a partir de form-initial-values."""
    if not valores:
        return {}
    itens = (valores.get("_embedded") or {}).get("fieldInitialValues") or []
    out = {}
    for it in itens:
        v = it.get("value")
        if v not in (None, ""):
            out[it["fieldId"]] = str(v).strip()
    return out


def _historico_de(historico: dict | None) -> list[dict]:
    """[{campo, itens:[{responsavel, data, atividade, texto}]}] de business-data/history."""
    if not historico:
        return []
    itens = (historico.get("_embedded") or {}).get("fieldHistoryValues") or []
    out = []
    for campo in itens:
        registros = [
            {
                "responsavel": (h.get("responsible") or "").strip(),
                "data": h.get("executionDate"),
                "atividade": (h.get("activityName") or "").strip(),
                "texto": (h.get("description") or "").strip(),
            }
            for h in campo.get("history") or []
            if (h.get("description") or "").strip()
        ]
        if registros:
            out.append({"campo": campo["field"], "itens": registros})
    return out


def _anexos_de(grid: dict | None) -> list[dict]:
    if not grid:
        return []
    linhas = (grid.get("_embedded") or {}).get("dataGridRows") or []
    return [linha.get("values", {}) for linha in linhas if linha.get("values")]


def _detalhes_de(detalhes: dict | None) -> dict:
    if not detalhes:
        return {}
    campos = ("title", "subtitle", "userName", "activityTitle", "activityStartDate",
              "activityCompleteDate", "activityDueDate", "processStartDate", "status",
              "approvalStatus", "processId", "version")
    return {c: detalhes.get(c) for c in campos if detalhes.get(c) is not None}


def _etapa_do_cabecalho(cabecalho: str) -> str:
    """'Abertura por Ana Clara em 18/09/2026' -> 'Abertura'."""
    m = re.match(r"\s*(.+?)\s+por\s+", cabecalho or "")
    return m.group(1).strip() if m else ""


def _responsavel_do_cabecalho(cabecalho: str) -> str:
    m = re.search(r"\bpor\s+(.+?)\s+em\s+", cabecalho or "")
    return m.group(1).strip() if m else ""


async def raspar_chamado(page: Page, coletor: ColetorApi, numero: int | str,
                         timeout_api_ms: int = 25_000) -> dict:
    """Abre o chamado, escolhe a atividade aprovada mais recente e devolve os dados."""
    numero = int(numero)
    agora = datetime.now().isoformat(timespec="seconds")
    atividades = await listar_atividades(page, numero)
    if not atividades:
        return {"processo": numero, "erro": "não foi possível abrir o chamado",
                "raspado_em": agora}

    cabecalho = await cabecalho_chamado(page)
    alvo = escolher_atividade(atividades)
    if alvo is None:
        # Regra do negócio: só a atividade aprovada tem conteúdo consolidado. Chamados
        # que só têm atividade em andamento não abrem o formulário, então são pulados.
        return {
            "processo": numero,
            "raspado_em": agora,
            "pulado": "sem atividade aprovada",
            "cabecalho": cabecalho,
            "atividades": atividades,
        }

    coletor.limpar()
    await page.goto(BASE + alvo["href"], wait_until="domcontentloaded")

    # espera as APIs essenciais chegarem, em vez de dormir um tempo fixo
    esperado = 0
    while esperado < timeout_api_ms and not coletor.completo():
        await page.wait_for_timeout(400)
        esperado += 400
    await page.wait_for_timeout(500)  # deixa a última resposta assentar

    dados: dict[str, Any] = {
        "processo": numero,
        "raspado_em": agora,
        "url": page.url,
        "cabecalho": cabecalho,
        "atividade_escolhida": alvo,
        "atividades": atividades,
        "detalhes": _detalhes_de(coletor.corpos.get("detalhes")),
        "campos": _campos_de(coletor.corpos.get("valores")),
        "historico": _historico_de(coletor.corpos.get("historico")),
        "anexos": _anexos_de(coletor.corpos.get("grid")),
        "fonte": "api",
    }

    if not dados["campos"] or not dados["historico"]:
        fr = next((f for f in page.frames if "form-web" in f.url), None)
        if fr:
            try:
                dom = await fr.evaluate(JS_DOM_FALLBACK)
                if not dados["campos"]:
                    dados["campos"] = dom.get("campos", {})
                    dados["fonte"] = "dom"
                if not dados["historico"] and dom.get("historico"):
                    dados["historico"] = [
                        {"campo": h["campo"],
                         "itens": [{"responsavel": _responsavel_do_cabecalho(i["cabecalho"]),
                                    "data": None,
                                    "atividade": _etapa_do_cabecalho(i["cabecalho"]),
                                    "texto": i["texto"].strip()} for i in h["itens"]]}
                        for h in dom["historico"]
                    ]
                    dados["fonte"] = "dom"
            except Exception as e:  # noqa: BLE001
                log.warning("reserva pelo DOM falhou no chamado %s: %s", numero, e)

    return dados
