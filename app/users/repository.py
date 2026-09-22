"""Acesso ao banco de usuários."""
from __future__ import annotations

import sqlite3

from app.auth.service import (
    gerar_api_key,
    get_password_hash,
    hash_api_key,
    verify_password,
)
from app.users.database import get_connection, init_db
from app.users.models import User


def _para_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        api_key_hash=row["api_key_hash"],
        api_key_prefix=row["api_key_prefix"],
        rate_limit_per_hour=row["rate_limit_per_hour"],
        is_admin=bool(row["is_admin"]),
        is_active=bool(row["is_active"]),
        criado_em=row["criado_em"],
    )


def get_user_by_username(database_path: str, username: str) -> User | None:
    conn = get_connection(database_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _para_user(row) if row else None
    finally:
        conn.close()


def get_user_by_api_key(database_path: str, chave: str) -> User | None:
    conn = get_connection(database_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE api_key_hash = ?",
                           (hash_api_key(chave),)).fetchone()
        return _para_user(row) if row else None
    finally:
        conn.close()


def list_users(database_path: str) -> list[User]:
    conn = get_connection(database_path)
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_para_user(r) for r in rows]
    finally:
        conn.close()


def create_user(database_path: str, username: str, password: str,
                rate_limit_per_hour: int, is_admin: bool = False) -> User:
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, rate_limit_per_hour, is_admin) "
            "VALUES (?, ?, ?, ?)",
            (username, get_password_hash(password), rate_limit_per_hour, int(is_admin)),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("Usuário já existe") from exc
    finally:
        conn.close()
    user = get_user_by_username(database_path, username)
    assert user is not None
    return user


def update_user(database_path: str, username: str, *, password: str | None = None,
                rate_limit_per_hour: int | None = None,
                is_active: bool | None = None) -> User | None:
    if not get_user_by_username(database_path, username):
        return None

    campos: list[str] = []
    valores: list[object] = []
    if password is not None:
        campos.append("password_hash = ?")
        valores.append(get_password_hash(password))
    if rate_limit_per_hour is not None:
        campos.append("rate_limit_per_hour = ?")
        valores.append(rate_limit_per_hour)
    if is_active is not None:
        campos.append("is_active = ?")
        valores.append(int(is_active))
    if not campos:
        return get_user_by_username(database_path, username)

    valores.append(username)
    conn = get_connection(database_path)
    try:
        conn.execute(f"UPDATE users SET {', '.join(campos)} WHERE username = ?", valores)
        conn.commit()
    finally:
        conn.close()
    return get_user_by_username(database_path, username)


def rotacionar_api_key(database_path: str, username: str) -> str | None:
    """Gera uma nova API key (invalidando a anterior) e devolve a chave em claro."""
    if not get_user_by_username(database_path, username):
        return None
    chave, hash_, prefixo = gerar_api_key()
    conn = get_connection(database_path)
    try:
        conn.execute("UPDATE users SET api_key_hash = ?, api_key_prefix = ? WHERE username = ?",
                     (hash_, prefixo, username))
        conn.commit()
    finally:
        conn.close()
    return chave


def revogar_api_key(database_path: str, username: str) -> bool:
    conn = get_connection(database_path)
    try:
        cur = conn.execute(
            "UPDATE users SET api_key_hash = NULL, api_key_prefix = NULL WHERE username = ?",
            (username,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def authenticate_user(database_path: str, username: str, password: str) -> User | None:
    user = get_user_by_username(database_path, username)
    if not user or not user.is_active:
        return None
    return user if verify_password(password, user.password_hash) else None


def increment_usage(database_path: str, username: str, endpoint: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO usage_counters (username, day, endpoint, count) "
            "VALUES (?, date('now'), ?, 1) "
            "ON CONFLICT (username, day, endpoint) DO UPDATE SET count = count + 1",
            (username, endpoint))
        conn.commit()
    finally:
        conn.close()


def get_usage(database_path: str, username: str | None = None,
              start_day: str | None = None, end_day: str | None = None) -> list[dict]:
    query = "SELECT username, day, endpoint, count FROM usage_counters WHERE 1=1"
    params: list[str] = []
    if username:
        query += " AND username = ?"
        params.append(username)
    if start_day:
        query += " AND day >= ?"
        params.append(start_day)
    if end_day:
        query += " AND day <= ?"
        params.append(end_day)
    query += " ORDER BY day DESC, username, endpoint"

    conn = get_connection(database_path)
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def preparar_banco(database_path: str, admin_username: str, admin_password: str,
                   rate_limit_per_hour: int) -> None:
    """Cria o esquema e o administrador inicial, se ainda não existir."""
    init_db(database_path)
    if not admin_password or get_user_by_username(database_path, admin_username):
        return
    create_user(database_path, admin_username, admin_password, rate_limit_per_hour, is_admin=True)
