"""Explorador do workspace Lecom: abre uma tela e despeja HTML, texto e screenshot de
todos os frames. Serve para remapear seletores quando a aplicação mudar.

Uso:
    python scripts/explorar_lecom.py                     # home
    python scripts/explorar_lecom.py --chamado 14764     # tela do chamado
    python scripts/explorar_lecom.py --chamado 14764 --abrir   # + formulário da atividade
    python scripts/explorar_lecom.py --url https://...   # uma URL qualquer
    python scripts/explorar_lecom.py --chamado 14764 --esperar 120   # tempo para inspecionar

Os dumps vão para data/debug/.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.browser import abrir_navegador, esperar_login  # noqa: E402
from app.scraper.config import DEBUG_DIR, URL_BASE  # noqa: E402
from app.scraper.lecom import (  # noqa: E402
    BASE,
    escolher_atividade,
    fechar_modais,
    listar_atividades,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

parser = argparse.ArgumentParser()
parser.add_argument("--chamado", help="número do chamado a abrir")
parser.add_argument("--abrir", action="store_true", help="abre a atividade aprovada mais recente")
parser.add_argument("--url", help="navega direto nesta URL em vez da home")
parser.add_argument("--tag", default="", help="sufixo para os arquivos de dump")
parser.add_argument("--esperar", type=int, default=0, help="segundos com a janela aberta ao final")
args = parser.parse_args()

DEBUG_DIR.mkdir(parents=True, exist_ok=True)
sufixo = f"_{args.tag}" if args.tag else ""


async def dump(page, nome: str) -> None:
    base = DEBUG_DIR / f"{nome}{sufixo}"
    base.with_suffix(".html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(base.with_suffix(".png")), full_page=False)
    try:
        texto = await page.evaluate("() => document.body.innerText")
        base.with_suffix(".txt").write_text(texto, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(f"  página: {page.url}")
    for i, fr in enumerate(page.frames):
        if fr is page.main_frame:
            continue
        try:
            conteudo = await fr.content()
        except Exception as e:  # noqa: BLE001
            print(f"  frame {i}: (ilegível: {e})")
            continue
        alvo = DEBUG_DIR / f"{nome}{sufixo}_frame{i}.html"
        alvo.write_text(conteudo, encoding="utf-8")
        print(f"  frame {i}: {fr.url[:110]} -> {alvo.name} ({len(conteudo) // 1024} KB)")


async def main() -> None:
    async with abrir_navegador(headless=False) as (ctx, page):
        destino = args.url or URL_BASE
        print(f"-> abrindo {destino}")
        await page.goto(destino, wait_until="domcontentloaded", timeout=90_000)
        await esperar_login(page)
        await page.wait_for_timeout(4000)
        await fechar_modais(page)
        await dump(page, "01_home")

        if args.chamado:
            print(f"-> abrindo chamado {args.chamado}")
            atividades = await listar_atividades(page, args.chamado)
            for a in atividades:
                print(f"   etapa {a['etapa']}/c{a['ciclo_url']} {a['atividade'][:30]:<32} "
                      f"{a['aberta_em']:<17} {a['status_texto']}")
            await dump(page, "02_chamado")

            if args.abrir and atividades:
                alvo = escolher_atividade(atividades) or atividades[0]
                print(f"-> abrindo atividade {alvo['href']}")
                await page.goto(BASE + alvo["href"], wait_until="domcontentloaded")
                await page.wait_for_timeout(9000)
                await dump(page, "03_atividade")

        if args.esperar:
            print(f"\njanela aberta por {args.esperar}s para inspeção manual...")
            await page.wait_for_timeout(args.esperar * 1000)


asyncio.run(main())
