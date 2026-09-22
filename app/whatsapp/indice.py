"""Índice leve dos chamados raspados, para consulta por número, telefone e cooperativa.

Os JSON de data/raw têm tudo, mas percorrer milhares de arquivos a cada mensagem seria
lento. Este índice SQLite guarda só o que a consulta precisa e é atualizado de forma
incremental pela data de modificação de cada arquivo. O detalhe completo continua sendo
lido do JSON na hora, então o índice pode ser apagado e recriado a qualquer momento.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.whatsapp.telefone import normalizar

log = logging.getLogger(__name__)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS chamados (
    processo      INTEGER PRIMARY KEY,
    cooperativa   TEXT,
    telefone      TEXT,               -- canônico, do campo LT_TELEFONE_SOLICITANTE
    solicitante   TEXT,
    assunto       TEXT,
    status        TEXT,
    abertura      TEXT,
    raspado_em    TEXT,
    arquivo_mtime REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ch_coop ON chamados(cooperativa);
CREATE INDEX IF NOT EXISTS idx_ch_tel  ON chamados(telefone);
"""

STATUS_CONHECIDOS = ("Aprovado", "Cancelado", "Rejeitado", "Em andamento", "Em alerta", "Atrasado")
_ultima_atualizacao: dict[str, float] = {}
INTERVALO_MIN_S = 60  # não varre a pasta mais que uma vez por minuto


def _status_do_cabecalho(cabecalho: str) -> str | None:
    for s in STATUS_CONHECIDOS:
        if s in (cabecalho or ""):
            return s
    return None


def _conn(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(caminho), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript(ESQUEMA)
    return conn


def _linha(dados: dict[str, Any], mtime: float) -> tuple | None:
    campos = dados.get("campos") or {}
    if not campos or dados.get("erro") or dados.get("pulado"):
        return None
    detalhes = dados.get("detalhes") or {}
    return (
        int(dados["processo"]),
        (campos.get("LT_COOPERATIVA2") or "").strip(),
        normalizar(campos.get("LT_TELEFONE_SOLICITANTE")),
        (campos.get("LT_NOME") or "").strip(),
        (campos.get("LT_ASSUNTO") or "").strip(),
        _status_do_cabecalho(dados.get("cabecalho", "")),
        (detalhes.get("processStartDate") or "")[:19],
        dados.get("raspado_em"),
        mtime,
    )


def atualizar(raw_dir: Path, caminho_idx: Path, forcar: bool = False) -> dict[str, int]:
    """Indexa os JSON novos ou alterados desde a última vez. Rápido quando nada mudou."""
    chave = str(caminho_idx)
    agora = time.time()
    if not forcar and agora - _ultima_atualizacao.get(chave, 0) < INTERVALO_MIN_S:
        return {"novos": 0, "pulados": 0}
    _ultima_atualizacao[chave] = agora

    conn = _conn(caminho_idx)
    try:
        conhecidos = {r["processo"]: r["arquivo_mtime"]
                      for r in conn.execute("SELECT processo, arquivo_mtime FROM chamados")}
        novos, pulados = 0, 0
        for arq in raw_dir.glob("*.json"):
            m = re.fullmatch(r"(\d+)\.json", arq.name)
            if not m:
                continue
            mtime = arq.stat().st_mtime
            if not forcar and conhecidos.get(int(m.group(1))) == mtime:
                continue
            try:
                linha = _linha(json.loads(arq.read_text(encoding="utf-8")), mtime)
            except (json.JSONDecodeError, KeyError, ValueError):
                pulados += 1
                continue
            if linha is None:
                pulados += 1
                continue
            conn.execute(
                "INSERT OR REPLACE INTO chamados (processo, cooperativa, telefone, solicitante, "
                "assunto, status, abertura, raspado_em, arquivo_mtime) VALUES (?,?,?,?,?,?,?,?,?)",
                linha)
            novos += 1
        conn.commit()
        if novos:
            log.info("índice WhatsApp: %d chamados novos/alterados", novos)
        return {"novos": novos, "pulados": pulados}
    finally:
        conn.close()


def buscar(caminho_idx: Path, processo: int) -> dict[str, Any] | None:
    conn = _conn(caminho_idx)
    try:
        r = conn.execute("SELECT * FROM chamados WHERE processo = ?", (processo,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def listar(caminho_idx: Path, cooperativa: str | None = None, telefones: set[str] | None = None,
           solicitante: str | None = None, limite: int = 5) -> list[dict[str, Any]]:
    """Chamados mais recentes dentro do escopo. `telefones`/`solicitante` restringem ao próprio."""
    cond, params = [], []
    if cooperativa:
        cond.append("cooperativa = ?")
        params.append(cooperativa)
    proprio = []
    if telefones:
        proprio.append("telefone IN (%s)" % ",".join("?" * len(telefones)))
        params.extend(sorted(telefones))
    if solicitante:
        proprio.append("lower(solicitante) = lower(?)")
        params.append(solicitante)
    if proprio:
        cond.append("(" + " OR ".join(proprio) + ")")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    conn = _conn(caminho_idx)
    try:
        rows = conn.execute(
            f"SELECT * FROM chamados {where} ORDER BY abertura DESC, processo DESC LIMIT ?",
            (*params, limite)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def total(caminho_idx: Path) -> int:
    conn = _conn(caminho_idx)
    try:
        return conn.execute("SELECT COUNT(*) FROM chamados").fetchone()[0]
    finally:
        conn.close()
