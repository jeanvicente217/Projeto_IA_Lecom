"""Indexa os chamados no banco vetorial.

A fonte padrão é a raspagem do workspace (data/raw). As planilhas de data/exports só entram
com --com-planilhas.

    python -m app.ingest                      # só a raspagem, incremental
    python -m app.ingest --limpar             # recria o índice do zero com a raspagem
    python -m app.ingest --com-planilhas      # raspagem + planilhas de data/exports
    python -m app.ingest --file caminho.xlsx  # uma planilha específica (sem raspagem)
    python -m app.ingest --forcar             # re-embeda tudo (ex.: trocou o modelo)
"""
import argparse
import json
import logging

from app.ingest.service import ingerir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

parser = argparse.ArgumentParser(description="Ingestão dos chamados no índice vetorial")
parser.add_argument("--file", "-f", help="indexa uma planilha específica (desliga a raspagem)")
parser.add_argument("--com-planilhas", action="store_true",
                    help="inclui os .xlsx de data/exports (por padrão só a raspagem é usada)")
parser.add_argument("--limpar", action="store_true",
                    help="recria o índice do zero, descartando o que já estava indexado")
parser.add_argument("--forcar", action="store_true", help="re-embeda todos os chamados")
args = parser.parse_args()

print(json.dumps(
    ingerir(args.file, forcar=args.forcar, com_planilhas=args.com_planilhas, limpar=args.limpar),
    ensure_ascii=False, indent=2))
