"""Diagnóstico: testa variações da raspagem (nº de abas, bloqueio de recursos) num
conjunto pequeno de chamados e relata o que falha e por quê.

Uso:
    python scripts/diagnostico_raspagem.py 14764 14760 14755 14750
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.browser import abrir_navegador, esperar_login  # noqa: E402
from app.scraper.config import DEBUG_DIR, URL_BASE  # noqa: E402
from app.scraper.extrator import ColetorApi, raspar_chamado  # noqa: E402
from app.scraper.lecom import fechar_modais  # noqa: E402
from app.scraper.runner import TIPOS_BLOQUEADOS, URLS_BLOQUEADAS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
NUMS = [int(x) for x in sys.argv[1:]] or [14764, 14760, 14755, 14750]
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


async def preparar(page, bloquear: bool) -> ColetorApi:
    if bloquear:
        async def rota(route):
            req = route.request
            if req.resource_type in TIPOS_BLOQUEADOS or any(u in req.url for u in URLS_BLOQUEADAS):
                return await route.abort()
            return await route.continue_()
        await page.route("**/*", rota)
    coletor = ColetorApi()
    page.on("response", lambda r: asyncio.create_task(coletor.on_response(r)))
    return coletor


async def cenario(ctx, nome: str, nums: list[int], abas: int, bloquear: bool) -> None:
    print(f"\n{'=' * 72}\n### cenário: {nome}  (abas={abas}, bloqueio={bloquear})")
    paginas = [await ctx.new_page() for _ in range(abas)]
    coletores = [await preparar(p, bloquear) for p in paginas]
    fila: asyncio.Queue[int] = asyncio.Queue()
    for n in nums:
        fila.put_nowait(n)
    resultados: list[dict] = []
    t0 = asyncio.get_event_loop().time()

    async def worker(page, coletor):
        while True:
            try:
                n = fila.get_nowait()
            except asyncio.QueueEmpty:
                return
            ini = asyncio.get_event_loop().time()
            try:
                d = await raspar_chamado(page, coletor, n)
                resultados.append({
                    "n": n, "s": round(asyncio.get_event_loop().time() - ini, 1),
                    "campos": len(d.get("campos", {})), "hist": len(d.get("historico", [])),
                    "apis": sorted(coletor.corpos), "fonte": d.get("fonte"),
                    "erro": d.get("erro"),
                })
            except Exception as e:  # noqa: BLE001
                resultados.append({"n": n, "s": round(asyncio.get_event_loop().time() - ini, 1),
                                   "campos": 0, "hist": 0, "apis": sorted(coletor.corpos),
                                   "erro": f"{type(e).__name__}"})

    await asyncio.gather(*(worker(p, c) for p, c in zip(paginas, coletores)))
    total = asyncio.get_event_loop().time() - t0
    for r in sorted(resultados, key=lambda r: r["n"], reverse=True):
        print(f"   {r['n']}  {r['s']:>5.1f}s  campos={r['campos']:<3} hist={r['hist']:<2} "
              f"fonte={str(r.get('fonte')):<5} apis={','.join(r['apis']) or '-':<38} {r.get('erro') or 'ok'}")
    ok = sum(1 for r in resultados if r["campos"])
    print(f"   -> {ok}/{len(nums)} ok em {total:.0f}s  ({len(nums) / total * 60:.1f} chamados/min)")
    for p in paginas:
        await p.close()


async def main():
    async with abrir_navegador(headless=False) as (ctx, page0):
        await page0.goto(URL_BASE, wait_until="domcontentloaded", timeout=90_000)
        await esperar_login(page0)
        await page0.wait_for_timeout(2500)
        await fechar_modais(page0)

        await cenario(ctx, "1 aba, sem bloqueio", NUMS, abas=1, bloquear=False)
        await cenario(ctx, "1 aba, com bloqueio", NUMS, abas=1, bloquear=True)
        await cenario(ctx, "2 abas, com bloqueio", NUMS, abas=2, bloquear=True)


asyncio.run(main())
