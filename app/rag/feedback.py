"""Registro das sugestões geradas e do feedback "a resposta foi útil?" dado sobre elas.

Cada resposta do /sugerir ganha um `id_sugestao`; quem integrou devolve esse id no
POST /feedback. Guardamos um retrato mínimo da sugestão (classificação, recomendação,
chamados usados) para que o feedback possa ser analisado depois sem reprocessar nada.
As tabelas ficam no mesmo SQLite dos usuários (DATABASE_PATH).
"""
from __future__ import annotations

import json
from typing import Any

from app.users.database import get_connection

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sugestoes (
    id            TEXT PRIMARY KEY,
    usuario       TEXT NOT NULL,
    assunto       TEXT NOT NULL,
    produto       TEXT,
    recomendacao  TEXT,
    confianca     REAL,
    similares     TEXT,                      -- JSON com os rótulos dos chamados usados (interno)
    criado_em     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Um feedback por sugestão: um novo envio substitui o anterior.
CREATE TABLE IF NOT EXISTS sugestoes_feedback (
    id_sugestao   TEXT PRIMARY KEY REFERENCES sugestoes(id) ON DELETE CASCADE,
    util          INTEGER NOT NULL,
    comentario    TEXT,
    usuario       TEXT NOT NULL,
    enviado_em    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sugestoes_data ON sugestoes(criado_em);
"""


def init_db(database_path: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.executescript(ESQUEMA)
        conn.commit()
    finally:
        conn.close()


def registrar_sugestao(database_path: str, id_: str, usuario: str, assunto: str,
                       produto: str | None, recomendacao: str | None, confianca: float | None,
                       similares: list[str]) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sugestoes (id, usuario, assunto, produto, recomendacao, "
            "confianca, similares) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_, usuario, assunto, produto, recomendacao, confianca,
             json.dumps(similares, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def buscar_sugestao(database_path: str, id_: str) -> dict[str, Any] | None:
    conn = get_connection(database_path)
    try:
        row = conn.execute("SELECT * FROM sugestoes WHERE id = ?", (id_,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def registrar_feedback(database_path: str, id_sugestao: str, util: bool,
                       comentario: str | None, usuario: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO sugestoes_feedback (id_sugestao, util, comentario, usuario) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id_sugestao) DO UPDATE SET util = excluded.util, "
            "comentario = excluded.comentario, usuario = excluded.usuario, "
            "enviado_em = datetime('now')",
            (id_sugestao, int(util), comentario, usuario))
        conn.commit()
    finally:
        conn.close()


def relatorio(database_path: str, dias: int = 30, limite_comentarios: int = 50) -> dict[str, Any]:
    """Totais de útil/não útil no período, por recomendação, e os últimos comentários negativos."""
    periodo = f"-{int(dias)} days"
    conn = get_connection(database_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM sugestoes WHERE criado_em >= datetime('now', ?)",
            (periodo,)).fetchone()[0]
        por_rec = conn.execute(
            "SELECT s.recomendacao, "
            "       SUM(CASE WHEN f.util = 1 THEN 1 ELSE 0 END) AS uteis, "
            "       SUM(CASE WHEN f.util = 0 THEN 1 ELSE 0 END) AS nao_uteis "
            "FROM sugestoes_feedback f JOIN sugestoes s ON s.id = f.id_sugestao "
            "WHERE s.criado_em >= datetime('now', ?) GROUP BY s.recomendacao",
            (periodo,)).fetchall()
        negativos = conn.execute(
            "SELECT s.id, s.assunto, s.produto, s.recomendacao, s.similares, f.comentario, "
            "       f.enviado_em "
            "FROM sugestoes_feedback f JOIN sugestoes s ON s.id = f.id_sugestao "
            "WHERE f.util = 0 AND s.criado_em >= datetime('now', ?) "
            "ORDER BY f.enviado_em DESC LIMIT ?",
            (periodo, int(limite_comentarios))).fetchall()
    finally:
        conn.close()

    uteis = sum(r["uteis"] or 0 for r in por_rec)
    nao_uteis = sum(r["nao_uteis"] or 0 for r in por_rec)
    avaliadas = uteis + nao_uteis
    return {
        "dias": dias,
        "sugestoes": total,
        "avaliadas": avaliadas,
        "uteis": uteis,
        "nao_uteis": nao_uteis,
        "taxa_utilidade": round(uteis / avaliadas, 3) if avaliadas else None,
        "por_recomendacao": {r["recomendacao"] or "-": {"uteis": r["uteis"] or 0,
                                                         "nao_uteis": r["nao_uteis"] or 0}
                             for r in por_rec},
        "nao_uteis_recentes": [
            {**dict(r), "similares": json.loads(r["similares"] or "[]")} for r in negativos
        ],
    }
