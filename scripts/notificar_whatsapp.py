"""Verifica mudanças de status nos chamados e avisa os contatos do WhatsApp com escopo
'proprio'. Seguro rodar com frequência: só notifica quando algo realmente mudou desde a
última checagem (a primeira vez que vê um chamado, só registra em silêncio).

Agende no Task Scheduler / cron, por exemplo a cada 15-30 minutos:
    python scripts/notificar_whatsapp.py

Equivalente a POST /whatsapp/notificar (admin).
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.config import get_settings  # noqa: E402
from app.whatsapp.notificacoes import verificar_e_notificar  # noqa: E402

print(json.dumps(verificar_e_notificar(get_settings()), ensure_ascii=False, indent=2))
