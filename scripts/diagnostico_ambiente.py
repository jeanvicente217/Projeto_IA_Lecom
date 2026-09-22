"""Investiga o 'Modo de ambiente inválido': compara navegação direta por URL x busca pela lupa,
repetindo cada chamado para saber se a falha é determinística.

Uso: python scripts/teste_ambiente.py 14760 14750 14740 14745
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.browser import abrir_navegador, esperar_login  # noqa: E402
from app.scraper.config import DEBUG_DIR, URL_BASE  # noqa: E402
from app.scraper.lecom import (  # noqa: E402
    SELETOR_LINK_ATIVIDADE,
    buscar_chamado,
    fechar_modais,
    url_flow,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
NUMS = [int(x) for x in sys.argv[1:]] or [14760, 14750]
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


async def estado_tela(page) -> str:
    try:
        txt = (await page.evaluate("() => document.body.innerText")).strip()
    except Exception:  # noqa: BLE001
        txt = ""
    if "ambiente inválido" in txt.lower():
        return "AMBIENTE_INVALIDO"
    if await page.locator(SELETOR_LINK_ATIVIDADE).count():
        return "OK"
    if "/flow/" not in page.url:
        return f"NAO_NAVEGOU ({page.url.split('bpm.nexum.com.br')[-1][:40]})"
    return "VAZIO/CARREGANDO"


async def main():
    async with abrir_navegador(headless=False) as (ctx, page):
        await page.goto(URL_BASE, wait_until="domcontentloaded", timeout=90_000)
        await esperar_login(page)
        await page.wait_for_timeout(3000)
        await fechar_modais(page)

        print(f"\n{'chamado':>8} {'via URL direta':<22} {'via lupa':<22}")
        for num in NUMS:
            await page.goto(url_flow(num), wait_until="domcontentloaded")
            await page.wait_for_timeout(7000)
            direta = await estado_tela(page)
            if direta != "OK":
                await page.screenshot(path=str(DEBUG_DIR / f"amb_{num}_direta.png"))

            await page.goto(URL_BASE, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            await fechar_modais(page)
            await buscar_chamado(page, num)
            await page.wait_for_timeout(2500)
            lupa = await estado_tela(page)
            if lupa != "OK":
                await page.screenshot(path=str(DEBUG_DIR / f"amb_{num}_lupa.png"))

            print(f"{num:>8} {direta:<22} {lupa:<22}")


asyncio.run(main())
