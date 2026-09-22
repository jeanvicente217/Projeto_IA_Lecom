"""Envia o resumo semanal (contagem de chamados por status) para os contatos do WhatsApp
com escopo 'cooperativa' — pensado para gestores que acompanham a empresa toda.

Agende no Task Scheduler / cron, uma vez por semana, ex. segunda-feira 8h:
    python scripts/resumo_semanal_whatsapp.py

Equivalente a POST /whatsapp/resumo-semanal (admin).
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.config import get_settings  # noqa: E402
from app.whatsapp.notificacoes import enviar_resumo_semanal  # noqa: E402

print(json.dumps(enviar_resumo_semanal(get_settings()), ensure_ascii=False, indent=2))
