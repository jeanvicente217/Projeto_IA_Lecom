"""Configuração do RPA de raspagem do workspace Lecom."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

URL_BASE = os.environ.get("LECOM_URL", "https://bpm.nexum.com.br/workspace/")

# Perfil dedicado do Chrome para o RPA (mantém a sessão entre execuções).
PERFIL_DIR = Path(os.environ.get("LECOM_PERFIL", ROOT / "data" / "chrome-profile"))
# Perfil real do usuário, copiado na primeira execução para herdar a sessão já logada.
PERFIL_ORIGEM = Path(
    os.environ.get("LECOM_PERFIL_ORIGEM", Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data")
)
PERFIL_NOME = os.environ.get("LECOM_PERFIL_NOME", "Default")

DATA_DIR = ROOT / "data"
DEBUG_DIR = DATA_DIR / "debug"
RAW_DIR = DATA_DIR / "raw"          # um .json por chamado raspado
RAW_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT_PADRAO = int(os.environ.get("LECOM_TIMEOUT", "45000"))
