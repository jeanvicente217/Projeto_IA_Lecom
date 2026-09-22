"""Avaliação leave-one-out da classificação.

Sorteia N chamados que têm produto, área e tipo preenchidos, chama a lógica de sugestão
excluindo o próprio chamado dos similares e compara produto/área/tipo previstos com o real.

Uso:
    python scripts/avaliar.py --n 100 [--seed 42] [--file data/exports/x.xlsx] [--api http://localhost:8000]
                              [--origem lecom|historico|todas]

Por padrão avalia só chamados de origem "lecom" (relatório recente): as áreas do histórico
2024-2025 usam nomes de squads antigos e não servem de gabarito para a área atual.
Sem --api usa a lógica em processo (precisa do .env); com --api chama o endpoint /sugerir.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.ingest.loader import list_exports, load_chamados  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--n", type=int, default=50)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--file", help="um export específico (default: todos em EXPORTS_PATH)")
parser.add_argument("--origem", default="lecom", choices=["lecom", "historico", "todas"])
parser.add_argument("--api", help="URL base da API; se omitido, roda em processo")
parser.add_argument("--out", default=str(ROOT / "data" / "avaliacao.csv"))
args = parser.parse_args()

s = get_settings()
arquivos = [Path(args.file)] if args.file else list_exports(s.exports_dir)
chamados = [
    c for p in arquivos for c in load_chamados(p)
    if c.produto and c.area and c.tipo and (args.origem == "todas" or c.origem == args.origem)
]
random.seed(args.seed)
amostra = random.sample(chamados, min(args.n, len(chamados)))
print(f"{len(chamados)} chamados elegíveis (origem={args.origem}); avaliando {len(amostra)}\n")


def prever(c):
    body = {
        "assunto": c.assunto,
        "descricao": c.descricao_cliente or None,
        "cliente": c.cliente,
        "excluir_processos": [c.doc_id],
    }
    if args.api:
        import httpx

        r = httpx.post(args.api.rstrip("/") + "/sugerir", json=body, timeout=120)
        r.raise_for_status()
        return r.json()
    from app.rag.schemas import SugestaoRequest
    from app.rag.service import sugerir

    return sugerir(SugestaoRequest(**body)).model_dump()


linhas = []
acertos = {"produto": 0, "area": 0, "tipo": 0}
t0 = time.time()
for i, c in enumerate(amostra, 1):
    try:
        r = prever(c)
    except Exception as e:  # noqa: BLE001
        print(f"[{i}] {c.processo} ERRO: {e}")
        continue
    cl = r["classificacao"]
    ok_p = cl["produto"].strip().lower() == c.produto.strip().lower()
    ok_a = cl["area_sugerida"].strip().lower() == c.area.strip().lower()
    ok_t = cl["tipo_chamado"].strip().lower() == c.tipo.strip().lower()
    acertos["produto"] += ok_p
    acertos["area"] += ok_a
    acertos["tipo"] += ok_t
    top = r["chamados_similares"][0]["similaridade"] if r["chamados_similares"] else 0
    linhas.append({
        "processo": c.rotulo, "assunto": c.assunto,
        "produto_real": c.produto, "produto_prev": cl["produto"], "ok_produto": ok_p,
        "area_real": c.area, "area_prev": cl["area_sugerida"], "ok_area": ok_a,
        "tipo_real": c.tipo, "tipo_prev": cl["tipo_chamado"], "ok_tipo": ok_t,
        "confianca": cl["confianca"], "recomendacao": r["recomendacao"],
        "n_dicas": len(r["dicas"]), "top_similaridade": top,
    })
    print(f"[{i}/{len(amostra)}] {c.rotulo} produto {'✔' if ok_p else '✘'} area {'✔' if ok_a else '✘'} tipo {'✔' if ok_t else '✘'}  | {c.assunto[:60]}")

n = len(linhas) or 1
print("\nRESULTADO")
for k, v in acertos.items():
    print(f"  {k:8s}: {v}/{n} = {v / n:.1%}")
print(f"  tempo médio: {(time.time() - t0) / n:.1f}s por chamado")

out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()) if linhas else ["processo"])
    w.writeheader()
    w.writerows(linhas)
print(f"  detalhes em {out}")
