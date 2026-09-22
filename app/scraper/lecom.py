"""Navegação no workspace Lecom (API assíncrona do Playwright).

Mapa das telas (descoberto na inspeção):
  /workspace/                       -> home (a aplicação fica no iframe #workspace-web-iframe)
  /workspace/flow/{numero}          -> abas Lista / Histórico / Fluxo do chamado;
                                       a tabela de atividades fica no documento principal
  /workspace/form-app/{n}/{etapa}/{ciclo}?isNewForm=true
                                    -> formulário da atividade, com o histórico do chamado

Etapas conhecidas: 2 = Abertura, 4 = Assumir atendimento, 5 = Executar atendimento.
Como as URLs são previsíveis, a busca pela lupa não é necessária.
"""
from __future__ import annotations

import logging
import re

from playwright.async_api import Frame, Page

log = logging.getLogger(__name__)

BASE = "https://bpm.nexum.com.br"
SELETOR_IFRAME = "#workspace-web-iframe"
SELETOR_BUSCA = "header input.ant-input"
SELETOR_LINK_ATIVIDADE = "a.col-with-link[href*='/form-app/']"
TEXTOS_FECHAR_MODAL = ("Continuar", "Entendi", "Fechar")

STATUS_APROVADO = "Aprovado"
# classe do rótulo -> status, para ler mesmo quando o texto não é acessível
CLASSES_STATUS = {
    "tag-process-status-approved": "Aprovado",
    "tag-process-status-on-alert": "Em alerta",
    "tag-process-status-late": "Atrasado",
    "tag-process-status-canceled": "Cancelado",
    "tag-process-status-rejected": "Rejeitado",
    "tag-process-status-in-progress": "Em andamento",
}


def url_flow(numero: str | int) -> str:
    return f"{BASE}/workspace/flow/{numero}"


def url_form(numero: str | int, etapa: int, ciclo: int) -> str:
    return f"{BASE}/workspace/form-app/{numero}/{etapa}/{ciclo}?isNewForm=true"


async def frame_app(page: Page) -> Frame | None:
    """Frame da home (a tela do chamado é renderizada no documento principal)."""
    el = await page.query_selector(SELETOR_IFRAME)
    if el:
        fr = await el.content_frame()
        if fr:
            return fr
    for fr in page.frames:
        if fr is not page.main_frame and "workspace-web" in fr.url:
            return fr
    return None


async def fechar_modais(page: Page) -> None:
    """Fecha o modal de boas-vindas e o painel de mensagens, que cobrem a tela."""
    alvos = [page, await frame_app(page)]
    for alvo in alvos:
        if alvo is None:
            continue
        for texto in TEXTOS_FECHAR_MODAL:
            try:
                btn = alvo.get_by_role("button", name=texto, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=3000)
                    log.info("modal fechado via botão %r", texto)
                    await page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                pass
        try:
            fechar = alvo.locator(".ant-modal-close, .ant-drawer-close")
            for i in range(min(await fechar.count(), 3)):
                if await fechar.nth(i).is_visible():
                    await fechar.nth(i).click(timeout=2000)
                    await page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass


async def buscar_chamado(page: Page, numero: str | int, espera_ms: int = 5000) -> None:
    """Busca pela lupa do cabeçalho: é o caminho que o workspace espera e que resolve
    o ambiente/versão do processo, ao contrário de navegar direto para /flow/{n}.

    Observação: não usar `fill()` aqui. O clique abre o painel de pesquisa avançada e o
    `fill` perde o foco; digitar pelo teclado é o que funciona.
    """
    campo = page.locator(SELETOR_BUSCA).first
    # timeout curto: se a lupa não está lá em poucos segundos, é sessão perdida, não lentidão
    await campo.wait_for(state="visible", timeout=12_000)
    await campo.click()
    await page.wait_for_timeout(700)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.keyboard.type(str(numero), delay=60)
    await page.wait_for_timeout(1500)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(espera_ms)


JS_ATIVIDADES = """
() => {
  const vistos = new Set();
  const linhas = [];
  for (const tr of document.querySelectorAll('tr')) {
    const link = tr.querySelector('a.col-with-link[href*="/form-app/"]');
    if (!link) continue;
    const href = link.getAttribute('href');
    if (vistos.has(href)) continue;          // a tabela vem duplicada (colunas fixas)
    const cels = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
    if (cels.length < 8) continue;           // a cópia fixa tem menos colunas
    vistos.add(href);
    const tag = tr.querySelector('span.tag-status');
    linhas.push({
      href,
      atividade: (link.getAttribute('title') || link.innerText).replace(/\\s+/g, ' ').trim(),
      ciclo: cels[1], responsavel: cels[2], aberta_em: cels[3], prazo: cels[4],
      finalizada_em: cels[5], tempo_total: cels[6], consumido: cels[7],
      status_texto: tag ? tag.innerText.trim() : '',
      status_classe: tag ? tag.className : '',
    });
  }
  return linhas;
}
"""

JS_CABECALHO = """
() => {
  const h = document.querySelector('header.sub-header') || document.querySelector('.sub-header');
  return h ? h.innerText.replace(/\\s+/g, ' ').trim() : '';
}
"""


class AmbienteInvalido(RuntimeError):
    """O workspace recusou o chamado com 'Modo de ambiente inválido'."""


async def _tela_ambiente_invalido(page: Page) -> bool:
    try:
        txt = await page.evaluate("() => document.body.innerText || ''")
    except Exception:  # noqa: BLE001
        return False
    return "ambiente inválido" in txt.lower()


async def _esperar_tabela(page: Page, timeout_ms: int) -> bool:
    """Espera a tabela de atividades, abortando cedo se a tela de erro aparecer."""
    passado = 0
    while passado < timeout_ms:
        if await page.locator(SELETOR_LINK_ATIVIDADE).count():
            return True
        if await _tela_ambiente_invalido(page):
            raise AmbienteInvalido("modo de ambiente inválido")
        await page.wait_for_timeout(400)
        passado += 400
    return False


async def listar_atividades(page: Page, numero: str | int, timeout_ms: int = 20_000,
                            tentar_lupa: bool = True) -> list[dict]:
    """Abre o chamado e devolve as atividades, da mais recente para a mais antiga.

    Tenta primeiro a URL direta (rápida). Se o workspace recusar com 'ambiente inválido',
    refaz pela lupa do cabeçalho, que é o caminho que a aplicação resolve por conta própria.
    """
    await page.goto(url_flow(numero), wait_until="domcontentloaded")
    try:
        achou = await _esperar_tabela(page, timeout_ms)
    except AmbienteInvalido:
        achou = False
        if not tentar_lupa:
            raise

    if not achou and tentar_lupa:
        log.info("chamado %s: URL direta não abriu; tentando pela lupa", numero)
        await page.goto(f"{BASE}/workspace/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await buscar_chamado(page, numero, espera_ms=3000)
        achou = await _esperar_tabela(page, timeout_ms)

    if not achou:
        return []
    await page.wait_for_timeout(600)

    linhas = await page.evaluate(JS_ATIVIDADES)
    for linha in linhas:
        m = re.search(r"/form-app/(\d+)/(\d+)/(\d+)", linha["href"])
        linha["etapa"] = int(m.group(2)) if m else None
        linha["ciclo_url"] = int(m.group(3)) if m else None
        if not linha["status_texto"]:
            for classe, rotulo in CLASSES_STATUS.items():
                if classe in linha["status_classe"]:
                    linha["status_texto"] = rotulo
                    break
    return linhas


async def cabecalho_chamado(page: Page) -> str:
    """Texto do cabeçalho da tela do chamado (título, status, quem abriu e quando)."""
    try:
        return await page.evaluate(JS_CABECALHO)
    except Exception:  # noqa: BLE001
        return ""


def escolher_atividade(atividades: list[dict]) -> dict | None:
    """A atividade aprovada mais recente (a tabela já vem ordenada por 'aberta em' desc)."""
    aprovadas = [a for a in atividades if a["status_texto"] == STATUS_APROVADO]
    return aprovadas[0] if aprovadas else None
