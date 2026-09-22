"""RPA que raspa o histórico dos chamados direto do workspace Lecom.

A lista de chamados vem de uma planilha exportada (coluna #Processo) ou de --numeros.
Cada chamado vira um JSON em data/raw/. A execução é retomável: chamados já raspados
com sucesso são pulados.

Uso:
    python scripts/raspar_lecom.py --planilha "C:\\...\\export (3).xlsx"
    python scripts/raspar_lecom.py --numeros 14764 14760 --abas 1
    python scripts/raspar_lecom.py --planilha ... --limite 50 --abas 4
    python scripts/raspar_lecom.py --planilha ... --forcar        # re-raspa tudo
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.config import RAW_DIR  # noqa: E402
from app.scraper.runner import ja_raspado, raspar  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")

parser = argparse.ArgumentParser(description="Raspagem dos chamados do workspace Lecom")
parser.add_argument("--planilha", help="xlsx com a coluna #Processo")
parser.add_argument("--numeros", nargs="*", type=int, help="números de chamado específicos")
parser.add_argument("--abas", type=int, default=3, help="abas em paralelo (padrão 3)")
parser.add_argument("--limite", type=int, help="processa no máximo N chamados")
parser.add_argument("--desde", type=int, help="apenas chamados com número >= este")
parser.add_argument("--forcar", action="store_true", help="re-raspa mesmo os já salvos")
parser.add_argument("--status", action="store_true", help="só mostra o andamento e sai")
parser.add_argument("--headless", action="store_true",
                    help="sem janela (exige sessão já salva em data/chrome-profile)")
args = parser.parse_args()


def numeros_da_planilha(caminho: str) -> list[int]:
    import pandas as pd

    raw = pd.read_excel(caminho, sheet_name=0, header=None)
    linha = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "#Processo"]
    if len(linha) == 0:
        raise SystemExit(f"Coluna '#Processo' não encontrada em {caminho}")
    col = pd.to_numeric(raw.iloc[linha[0] + 1 :, 0], errors="coerce").dropna()
    return sorted({int(x) for x in col}, reverse=True)


if args.numeros:
    numeros = args.numeros
elif args.planilha:
    numeros = numeros_da_planilha(args.planilha)
else:
    parser.error("informe --planilha ou --numeros")

if args.desde:
    numeros = [n for n in numeros if n >= args.desde]

feitos = [n for n in numeros if ja_raspado(n)]
print(f"{len(numeros)} chamados na lista | {len(feitos)} já raspados | "
      f"{len(numeros) - len(feitos)} pendentes | arquivos em {RAW_DIR}")

if args.status:
    falhas = [p.stem for p in RAW_DIR.glob("*.json")
              if "erro" in json.loads(p.read_text(encoding="utf-8"))]
    print(f"{len(falhas)} com erro" + (f": {falhas[:20]}" if falhas else ""))
    raise SystemExit

pendentes = [n for n in numeros if args.forcar or not ja_raspado(n)]
if args.limite:
    pendentes = pendentes[: args.limite]

stats = raspar(pendentes, abas=args.abas, forcar=args.forcar, headless=args.headless)
print(json.dumps(stats, ensure_ascii=False, indent=2))
