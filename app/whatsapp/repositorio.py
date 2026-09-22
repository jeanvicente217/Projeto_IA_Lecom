"""Contatos autorizados no WhatsApp e todo o estado do bot: eventos processados,
conversas pendentes de confirmação, pré-chamados abertos por descrição livre,
notificações proativas já enviadas e perguntas de satisfação já feitas.

O cadastro de contatos é a lista de permissão: um telefone que não está aqui (ou está
inativo) não recebe nenhum dado, só uma mensagem genérica. Cada contato tem um escopo:

  proprio      -> só chamados abertos pelo próprio telefone (ou pelo nome cadastrado)
  cooperativa  -> todos os chamados da cooperativa/empresa do contato

As tabelas vivem no mesmo SQLite dos usuários da API (DATABASE_PATH), para um backup só.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.users.database import get_connection
from app.whatsapp.telefone import normalizar

ESCOPOS = ("proprio", "cooperativa")
STATUS_PRE_CHAMADO = ("pendente", "aberto", "descartado")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS whatsapp_contatos (
    telefone     TEXT PRIMARY KEY,           -- canônico: 55 + DDD + número
    nome         TEXT NOT NULL,
    cooperativa  TEXT NOT NULL,              -- igual ao campo Cliente (LT_COOPERATIVA2) do chamado
    escopo       TEXT NOT NULL DEFAULT 'proprio',
    ativo        INTEGER NOT NULL DEFAULT 1,
    criado_em    TEXT NOT NULL DEFAULT (datetime('now')),
    ultimo_uso   TEXT
);

CREATE TABLE IF NOT EXISTS whatsapp_eventos (
    id_mensagem  TEXT PRIMARY KEY,           -- id que o Evolution manda; evita processar 2x
    telefone     TEXT NOT NULL,
    recebido_em  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_wa_eventos_data ON whatsapp_eventos(recebido_em);

-- Conversa com estado: uma pendência por telefone (confirmar abertura, pergunta de
-- satisfação). Curta duração (expira_em); nunca guarda mais de uma por telefone.
CREATE TABLE IF NOT EXISTS whatsapp_sessoes (
    telefone   TEXT PRIMARY KEY,
    tipo       TEXT NOT NULL,
    dados      TEXT NOT NULL,                -- JSON
    criado_em  TEXT NOT NULL DEFAULT (datetime('now')),
    expira_em  TEXT NOT NULL
);

-- Descrições de problema recebidas por texto livre no WhatsApp, que o cliente confirmou
-- querer registrar. Fica pendente para um humano transformar em chamado formal no Lecom
-- (não escrevemos direto no Lecom: é uma fila de triagem, não abertura automática).
CREATE TABLE IF NOT EXISTS whatsapp_pre_chamados (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    telefone       TEXT NOT NULL,
    nome           TEXT NOT NULL,
    cooperativa    TEXT NOT NULL,
    assunto        TEXT NOT NULL,
    descricao      TEXT NOT NULL,
    classificacao  TEXT,                     -- JSON: produto/tipo/area/criticidade/confianca
    origem         TEXT NOT NULL DEFAULT 'sugestao',  -- 'sugestao' | 'reabertura'
    status         TEXT NOT NULL DEFAULT 'pendente',  -- pendente | aberto | descartado
    numero_chamado TEXT,
    observacao     TEXT,
    criado_em      TEXT NOT NULL DEFAULT (datetime('now')),
    atualizado_em  TEXT
);

-- Um registro por (telefone, chamado) com o hash do último estado notificado, para o
-- aviso proativo de mudança de status não repetir e não disparar no primeiro sinal.
CREATE TABLE IF NOT EXISTS whatsapp_notificacoes (
    telefone      TEXT NOT NULL,
    processo      INTEGER NOT NULL,
    hash_estado   TEXT NOT NULL,
    notificado_em TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (telefone, processo)
);

-- Controla a pergunta "isso resolveu?": uma vez por (telefone, chamado).
CREATE TABLE IF NOT EXISTS whatsapp_satisfacao (
    telefone      TEXT NOT NULL,
    processo      INTEGER NOT NULL,
    perguntado_em TEXT NOT NULL DEFAULT (datetime('now')),
    respondido    TEXT,
    PRIMARY KEY (telefone, processo)
);
"""


@dataclass
class Contato:
    telefone: str
    nome: str
    cooperativa: str
    escopo: str
    ativo: bool
    criado_em: str | None = None
    ultimo_uso: str | None = None


def init_db(database_path: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.executescript(ESQUEMA)
        conn.commit()
    finally:
        conn.close()


def _para_contato(row: sqlite3.Row) -> Contato:
    return Contato(telefone=row["telefone"], nome=row["nome"], cooperativa=row["cooperativa"],
                   escopo=row["escopo"], ativo=bool(row["ativo"]),
                   criado_em=row["criado_em"], ultimo_uso=row["ultimo_uso"])


def buscar_contato(database_path: str, telefone: str) -> Contato | None:
    canonico = normalizar(telefone)
    if not canonico:
        return None
    conn = get_connection(database_path)
    try:
        row = conn.execute("SELECT * FROM whatsapp_contatos WHERE telefone = ?",
                           (canonico,)).fetchone()
        return _para_contato(row) if row else None
    finally:
        conn.close()


def listar_contatos(database_path: str) -> list[Contato]:
    conn = get_connection(database_path)
    try:
        return [_para_contato(r) for r in
                conn.execute("SELECT * FROM whatsapp_contatos ORDER BY cooperativa, nome").fetchall()]
    finally:
        conn.close()


def salvar_contato(database_path: str, telefone: str, nome: str, cooperativa: str,
                   escopo: str = "proprio", ativo: bool = True) -> Contato:
    """Cria ou atualiza (o telefone é a chave)."""
    canonico = normalizar(telefone)
    if not canonico:
        raise ValueError("Telefone inválido: informe DDD e número, ex.: 14991449422")
    if escopo not in ESCOPOS:
        raise ValueError(f"Escopo deve ser um de {ESCOPOS}")
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO whatsapp_contatos (telefone, nome, cooperativa, escopo, ativo) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(telefone) DO UPDATE SET nome=excluded.nome, "
            "cooperativa=excluded.cooperativa, escopo=excluded.escopo, ativo=excluded.ativo",
            (canonico, nome.strip(), cooperativa.strip(), escopo, int(ativo)))
        conn.commit()
    finally:
        conn.close()
    contato = buscar_contato(database_path, canonico)
    assert contato is not None
    return contato


def remover_contato(database_path: str, telefone: str) -> bool:
    canonico = normalizar(telefone)
    if not canonico:
        return False
    conn = get_connection(database_path)
    try:
        cur = conn.execute("DELETE FROM whatsapp_contatos WHERE telefone = ?", (canonico,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def registrar_uso(database_path: str, telefone: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute("UPDATE whatsapp_contatos SET ultimo_uso = datetime('now') WHERE telefone = ?",
                     (telefone,))
        conn.commit()
    finally:
        conn.close()


def marcar_evento(database_path: str, id_mensagem: str, telefone: str) -> bool:
    """Devolve True se a mensagem é nova; False se já foi processada (o Evolution reenvia)."""
    if not id_mensagem:
        return True
    conn = get_connection(database_path)
    try:
        try:
            conn.execute("INSERT INTO whatsapp_eventos (id_mensagem, telefone) VALUES (?, ?)",
                         (id_mensagem, telefone))
            conn.commit()
        except sqlite3.IntegrityError:
            return False
        # mantém a tabela pequena: só os últimos 7 dias interessam para deduplicar
        conn.execute("DELETE FROM whatsapp_eventos WHERE recebido_em < datetime('now', '-7 days')")
        conn.commit()
        return True
    finally:
        conn.close()


# ----------------------------------------------------------------------------- sessões (estado)

def salvar_estado(database_path: str, telefone: str, tipo: str, dados: dict[str, Any],
                  ttl_min: int = 30) -> None:
    """Guarda uma pendência de conversa (ex.: aguardando sim/não). Substitui a anterior."""
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO whatsapp_sessoes (telefone, tipo, dados, expira_em) "
            "VALUES (?, ?, ?, datetime('now', ?)) "
            "ON CONFLICT(telefone) DO UPDATE SET tipo = excluded.tipo, dados = excluded.dados, "
            "criado_em = datetime('now'), expira_em = excluded.expira_em",
            (telefone, tipo, json.dumps(dados, ensure_ascii=False), f"+{int(ttl_min)} minutes"))
        conn.commit()
    finally:
        conn.close()


def obter_estado(database_path: str, telefone: str) -> dict[str, Any] | None:
    """Devolve {'tipo':..., 'dados':...} se houver pendência válida; None caso contrário."""
    conn = get_connection(database_path)
    try:
        row = conn.execute(
            "SELECT tipo, dados FROM whatsapp_sessoes WHERE telefone = ? AND expira_em > datetime('now')",
            (telefone,)).fetchone()
        if not row:
            conn.execute("DELETE FROM whatsapp_sessoes WHERE telefone = ? AND expira_em <= datetime('now')",
                        (telefone,))
            conn.commit()
            return None
        return {"tipo": row["tipo"], "dados": json.loads(row["dados"])}
    finally:
        conn.close()


def limpar_estado(database_path: str, telefone: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute("DELETE FROM whatsapp_sessoes WHERE telefone = ?", (telefone,))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------- pré-chamados

def criar_pre_chamado(database_path: str, telefone: str, nome: str, cooperativa: str,
                      assunto: str, descricao: str, classificacao: dict[str, Any] | None,
                      origem: str = "sugestao") -> int:
    """Fila de triagem: um humano converte em chamado formal no Lecom. Não escrevemos no
    Lecom diretamente — abrir chamado em nome do cliente sem revisão é arriscado demais."""
    conn = get_connection(database_path)
    try:
        cur = conn.execute(
            "INSERT INTO whatsapp_pre_chamados (telefone, nome, cooperativa, assunto, descricao, "
            "classificacao, origem) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (telefone, nome, cooperativa, assunto, descricao,
             json.dumps(classificacao, ensure_ascii=False) if classificacao else None, origem))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def listar_pre_chamados(database_path: str, status: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection(database_path)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM whatsapp_pre_chamados WHERE status = ? ORDER BY criado_em DESC",
                (status,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM whatsapp_pre_chamados ORDER BY criado_em DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("classificacao"):
                try:
                    d["classificacao"] = json.loads(d["classificacao"])
                except json.JSONDecodeError:
                    d["classificacao"] = None
            out.append(d)
        return out
    finally:
        conn.close()


def buscar_pre_chamado(database_path: str, id_: int) -> dict[str, Any] | None:
    itens = listar_pre_chamados(database_path)
    return next((p for p in itens if p["id"] == id_), None)


def atualizar_pre_chamado(database_path: str, id_: int, status: str,
                          numero_chamado: str | None = None, observacao: str | None = None) -> bool:
    conn = get_connection(database_path)
    try:
        cur = conn.execute(
            "UPDATE whatsapp_pre_chamados SET status = ?, "
            "numero_chamado = COALESCE(?, numero_chamado), observacao = COALESCE(?, observacao), "
            "atualizado_em = datetime('now') WHERE id = ?",
            (status, numero_chamado, observacao, id_))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ----------------------------------------------------------------------------- notificações proativas

def hash_notificado(database_path: str, telefone: str, processo: int) -> str | None:
    conn = get_connection(database_path)
    try:
        row = conn.execute(
            "SELECT hash_estado FROM whatsapp_notificacoes WHERE telefone = ? AND processo = ?",
            (telefone, processo)).fetchone()
        return row["hash_estado"] if row else None
    finally:
        conn.close()


def marcar_notificado(database_path: str, telefone: str, processo: int, hash_estado: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute(
            "INSERT INTO whatsapp_notificacoes (telefone, processo, hash_estado) VALUES (?, ?, ?) "
            "ON CONFLICT(telefone, processo) DO UPDATE SET hash_estado = excluded.hash_estado, "
            "notificado_em = datetime('now')",
            (telefone, processo, hash_estado))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------- satisfação

def satisfacao_ja_perguntada(database_path: str, telefone: str, processo: int) -> bool:
    conn = get_connection(database_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM whatsapp_satisfacao WHERE telefone = ? AND processo = ?",
            (telefone, processo)).fetchone()
        return row is not None
    finally:
        conn.close()


def registrar_pergunta_satisfacao(database_path: str, telefone: str, processo: int) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute("INSERT OR IGNORE INTO whatsapp_satisfacao (telefone, processo) VALUES (?, ?)",
                    (telefone, processo))
        conn.commit()
    finally:
        conn.close()


def registrar_resposta_satisfacao(database_path: str, telefone: str, processo: int,
                                  respondido: str) -> None:
    conn = get_connection(database_path)
    try:
        conn.execute("UPDATE whatsapp_satisfacao SET respondido = ? WHERE telefone = ? AND processo = ?",
                    (respondido, telefone, processo))
        conn.commit()
    finally:
        conn.close()
