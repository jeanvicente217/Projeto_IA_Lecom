"""Orquestra a raspagem de muitos chamados: abas em paralelo (asyncio), retomável."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from app.scraper.browser import abrir_navegador, esperar_login, logado
from app.scraper.config import RAW_DIR, URL_BASE
from app.scraper.extrator import ColetorApi, raspar_chamado
from app.scraper.lecom import fechar_modais

log = logging.getLogger(__name__)

# Falhas seguidas que disparam a checagem de sessão. A sessão do Lecom cai depois de
# algumas horas e, sem isso, o RPA gastaria um timeout por chamado até o fim da lista.
FALHAS_ATE_CHECAR_SESSAO = 6


class SessaoPerdida(RuntimeError):
    """A sessão do workspace caiu no meio da raspagem."""

# recursos que não afetam as APIs e só gastam tempo/banda
TIPOS_BLOQUEADOS = {"image", "media", "font"}
URLS_BLOQUEADAS = ("/social-service/", "google-analytics", "/modelista/")


def caminho_json(numero: int | str) -> Path:
    return RAW_DIR / f"{numero}.json"


def ja_raspado(numero: int | str) -> bool:
    """Já processado: raspado com sucesso ou deliberadamente pulado (sem atividade aprovada)."""
    p = caminho_json(numero)
    if not p.exists() or p.stat().st_size < 80:
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return "erro" not in d


def salvar(numero: int, dados: dict) -> None:
    caminho_json(numero).write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


async def _preparar_pagina(page) -> ColetorApi:
    async def rota(route):
        req = route.request
        if req.resource_type in TIPOS_BLOQUEADOS or any(u in req.url for u in URLS_BLOQUEADAS):
            return await route.abort()
        return await route.continue_()

    await page.route("**/*", rota)
    coletor = ColetorApi()
    page.on("response", lambda r: asyncio.create_task(coletor.on_response(r)))
    return coletor


async def _raspar_async(numeros: list[int], abas: int, tentativas: int,
                        progresso_a_cada: int, headless: bool = False) -> dict:
    fila: asyncio.Queue[int] = asyncio.Queue()
    for n in numeros:
        fila.put_nowait(n)

    stats = {"total": len(numeros), "ok": 0, "falha": 0, "sem_aprovada": 0}
    inicio = time.time()
    parar = asyncio.Event()
    falhas_seguidas = 0

    async with abrir_navegador(headless=headless) as (ctx, page0):
        await page0.goto(URL_BASE, wait_until="domcontentloaded", timeout=90_000)
        await esperar_login(page0)
        await page0.wait_for_timeout(2500)
        await fechar_modais(page0)

        paginas = [page0] + [await ctx.new_page() for _ in range(max(0, abas - 1))]
        coletores = [await _preparar_pagina(p) for p in paginas]

        async def trabalhador(page, coletor) -> None:
            nonlocal falhas_seguidas
            while not parar.is_set():
                try:
                    numero = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                erro, pulado = None, None
                for tentativa in range(1, tentativas + 1):
                    try:
                        dados = await raspar_chamado(page, coletor, numero)
                        pulado = dados.get("pulado")
                        erro = dados.get("erro") or (
                            None if (pulado or dados.get("campos")) else "sem campos")
                        # "não abriu" já esgotou URL direta e lupa: retentar só gasta tempo
                        if erro and erro.startswith("não foi possível") :
                            dados["erro"] = erro
                            salvar(numero, dados)
                            break
                        if erro and tentativa < tentativas:
                            await page.wait_for_timeout(1500)
                            continue
                        if erro:
                            dados["erro"] = erro
                        salvar(numero, dados)
                        break
                    except Exception as e:  # noqa: BLE001
                        erro = f"{type(e).__name__}: {e}"[:400]
                        if tentativa < tentativas:
                            log.info("chamado %s tentativa %d falhou (%s)", numero, tentativa,
                                     type(e).__name__)
                            await page.wait_for_timeout(2000)
                            continue
                        salvar(numero, {"processo": numero, "erro": erro,
                                        "raspado_em": datetime.now().isoformat(timespec="seconds")})

                if erro:
                    stats["falha"] += 1
                    falhas_seguidas += 1
                    log.warning("chamado %s: %s", numero, erro)
                    # muitas falhas seguidas quase sempre são a sessão caindo, não os chamados
                    if falhas_seguidas >= FALHAS_ATE_CHECAR_SESSAO and not await logado(page):
                        parar.set()
                        log.error(
                            "Sessão do workspace perdida após %d chamados. A raspagem foi "
                            "interrompida; o progresso está salvo. Rode de novo SEM --headless "
                            "para refazer o login e continuar de onde parou.",
                            stats["ok"] + stats["sem_aprovada"])
                        return
                elif pulado:
                    stats["sem_aprovada"] += 1
                    falhas_seguidas = 0
                else:
                    stats["ok"] += 1
                    falhas_seguidas = 0

                feitos = stats["ok"] + stats["falha"] + stats["sem_aprovada"]
                if feitos % progresso_a_cada == 0 or feitos == len(numeros):
                    passado = time.time() - inicio
                    taxa = feitos / passado if passado else 0
                    restam = (len(numeros) - feitos) / taxa / 60 if taxa else 0
                    log.info("%d/%d ok=%d sem_aprovada=%d falha=%d | %.1f/min | ~%.0f min restantes",
                             feitos, len(numeros), stats["ok"], stats["sem_aprovada"],
                             stats["falha"], taxa * 60, restam)

        await asyncio.gather(*(trabalhador(p, c) for p, c in zip(paginas, coletores)))

    stats["minutos"] = round((time.time() - inicio) / 60, 1)
    stats["interrompido_por_sessao"] = parar.is_set()
    log.info("Fim: ok=%d sem_aprovada=%d falha=%d em %.1f min%s",
             stats["ok"], stats["sem_aprovada"], stats["falha"], stats["minutos"],
             " (interrompido: sessão perdida)" if parar.is_set() else "")
    return stats


def raspar(numeros: list[int], abas: int = 3, forcar: bool = False, tentativas: int = 2,
           progresso_a_cada: int = 25, headless: bool = False) -> dict:
    """Raspa a lista de chamados (pulando os já salvos) e devolve estatísticas.

    `headless=True` só funciona quando a sessão já está salva em data/chrome-profile,
    porque o login manual precisa de janela visível.
    """
    pendentes = [n for n in numeros if forcar or not ja_raspado(n)]
    pulados = len(numeros) - len(pendentes)
    log.info("Raspagem: %d na lista, %d já feitos, %d a processar, %d abas%s",
             len(numeros), pulados, len(pendentes), abas, " (headless)" if headless else "")
    if not pendentes:
        return {"total": len(numeros), "pendentes": 0, "pulados": pulados, "ok": 0,
                "falha": 0, "sem_aprovada": 0}
    stats = asyncio.run(_raspar_async(pendentes, abas, tentativas, progresso_a_cada, headless))
    stats["pulados"] = pulados
    return stats
