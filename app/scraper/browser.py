"""Abertura do Chrome com a sessão do usuário para o RPA do Lecom (API assíncrona).

Usamos asyncio (e não threads) porque a API síncrona do Playwright não é thread-safe:
várias abas em threads abortam as navegações umas das outras.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Page, async_playwright

from app.scraper.config import (
    PERFIL_DIR,
    PERFIL_NOME,
    PERFIL_ORIGEM,
    TIMEOUT_PADRAO,
    URL_BASE,
)

log = logging.getLogger(__name__)

# arquivos que carregam a sessão (cookies/credenciais) do perfil real
ARQUIVOS_SESSAO = [
    "Local State",
    f"{PERFIL_NOME}/Preferences",
    f"{PERFIL_NOME}/Network/Cookies",
    f"{PERFIL_NOME}/Network/Network Persistent State",
    f"{PERFIL_NOME}/Login Data",
    f"{PERFIL_NOME}/Web Data",
]


def copiar_sessao() -> bool:
    """Copia cookies e credenciais do perfil real do Chrome para o perfil do RPA.

    Só roda quando o perfil do RPA ainda não existe. Com o Chrome aberto alguns arquivos
    ficam travados; nesse caso o login é feito uma vez na janela e passa a persistir.
    """
    if PERFIL_DIR.exists():
        return False
    if not PERFIL_ORIGEM.exists():
        log.warning("Perfil de origem não encontrado em %s; será preciso logar na janela", PERFIL_ORIGEM)
        return False

    copiados = 0
    for rel in ARQUIVOS_SESSAO:
        origem, destino = PERFIL_ORIGEM / rel, PERFIL_DIR / rel
        if not origem.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(origem, destino)
            copiados += 1
        except OSError as e:
            log.info("sem copiar %s (%s)", rel, type(e).__name__)
    log.info("sessão copiada do perfil '%s' (%d arquivos)", PERFIL_NOME, copiados)
    return copiados > 0


def _chrome_path() -> str | None:
    if sys.platform != "win32":
        return None
    for p in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ):
        if p.exists():
            return str(p)
    return None


@asynccontextmanager
async def abrir_navegador(headless: bool = False):
    """Abre o Chrome com o perfil do RPA. Devolve (contexto, primeira página)."""
    copiar_sessao()
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        kwargs = dict(
            user_data_dir=str(PERFIL_DIR),
            headless=headless,
            viewport={"width": 1500, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        chrome = _chrome_path()
        if chrome:
            kwargs["executable_path"] = chrome
        ctx = await pw.chromium.launch_persistent_context(**kwargs)
        ctx.set_default_timeout(TIMEOUT_PADRAO)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            yield ctx, page
        finally:
            await ctx.close()


async def logado(page: Page) -> bool:
    """A lupa do cabeçalho só existe depois de autenticado."""
    try:
        return await page.locator("header input.ant-input").first.is_visible(timeout=3000)
    except Exception:  # noqa: BLE001
        return False


async def esperar_login(page: Page, timeout_s: int = 600) -> None:
    """Se a sessão não estiver válida, espera o usuário logar na janela aberta."""
    for _ in range(5):  # a SPA demora a montar o cabeçalho
        if await logado(page):
            return
        await page.wait_for_timeout(1500)

    print("\n" + "=" * 70)
    print("LOGIN NECESSÁRIO: informe usuário e senha na janela do Chrome que abriu.")
    print(f"URL: {URL_BASE}")
    print("A raspagem continua sozinha assim que a tela inicial carregar.")
    print("=" * 70 + "\n", flush=True)

    limite = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < limite:
        if await logado(page):
            print("-> sessão autenticada; seguindo com a raspagem\n", flush=True)
            return
        await page.wait_for_timeout(2000)
    raise TimeoutError("Login não concluído no tempo esperado")
