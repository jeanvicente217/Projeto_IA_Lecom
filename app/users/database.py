"""Banco SQLite de usuários, API keys e contadores de uso."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    api_key_hash        TEXT UNIQUE,
    api_key_prefix      TEXT,
    rate_limit_per_hour INTEGER NOT NULL DEFAULT 120,
    is_admin            INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    criado_em           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage_counters (
    username TEXT NOT NULL,
    day      TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (username, day, endpoint)
);

CREATE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key_hash);
"""


def get_connection(database_path: str) -> sqlite3.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(database_path: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.executescript(ESQUEMA)
        conn.commit()
    finally:
        conn.close()
