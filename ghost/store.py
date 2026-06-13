"""SQLite residue store for GHOST.

Append-only audit log of sessions and the actions executed within them. Each
action carries a hash of its params/response and an Ed25519 signature. At
evaporate time a root signature is computed over the ordered action chain,
making any later tampering detectable.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

GHOST_HOME = Path(os.environ.get("GHOST_HOME", Path.home() / ".ghost"))
DEFAULT_DB = GHOST_HOME / "residue.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    intent          TEXT NOT NULL,
    scopes          TEXT NOT NULL DEFAULT '',
    public_key      TEXT NOT NULL,
    spawned_at      TEXT NOT NULL,
    ttl_seconds     INTEGER NOT NULL,
    expires_at      TEXT NOT NULL,
    evaporated_at   TEXT,
    lived_seconds   REAL,
    root_signature  TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    action_id       TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    tool            TEXT NOT NULL,
    action          TEXT NOT NULL,
    params_hash     TEXT NOT NULL,
    response_hash   TEXT,
    http_status     INTEGER,
    decision        TEXT NOT NULL DEFAULT 'executed',
    timestamp       TEXT NOT NULL,
    signature       TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS credentials_log (
    session_id      TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL,
    event           TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_spawned ON sessions(spawned_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id, seq);
"""


class ResidueStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- sessions ---------------------------------------------------------
    def insert_session(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO sessions
               (session_id, intent, scopes, public_key, spawned_at,
                ttl_seconds, expires_at)
               VALUES (:session_id, :intent, :scopes, :public_key, :spawned_at,
                       :ttl_seconds, :expires_at)""",
            row,
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        return cur.fetchone()

    def finalize_session(
        self, session_id: str, evaporated_at: str, lived_seconds: float, root_signature: str
    ) -> None:
        self._conn.execute(
            """UPDATE sessions
               SET evaporated_at = ?, lived_seconds = ?, root_signature = ?
               WHERE session_id = ?""",
            (evaporated_at, lived_seconds, root_signature, session_id),
        )
        self._conn.commit()

    def list_sessions(self, limit: int = 50) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions ORDER BY spawned_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    # ---- actions ----------------------------------------------------------
    def next_seq(self, session_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM actions WHERE session_id = ?",
            (session_id,),
        )
        return int(cur.fetchone()["n"])

    def insert_action(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO actions
               (action_id, session_id, seq, tool, action, params_hash,
                response_hash, http_status, decision, timestamp, signature)
               VALUES (:action_id, :session_id, :seq, :tool, :action, :params_hash,
                       :response_hash, :http_status, :decision, :timestamp, :signature)""",
            row,
        )
        self._conn.commit()

    def actions_for(self, session_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM actions WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        )
        return cur.fetchall()

    def count_actions(self, session_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS c FROM actions WHERE session_id = ?", (session_id,)
        )
        return int(cur.fetchone()["c"])

    # ---- credentials ------------------------------------------------------
    def log_credential(self, session_id: str, fingerprint: str, event: str, ts: str) -> None:
        self._conn.execute(
            """INSERT INTO credentials_log (session_id, key_fingerprint, event, timestamp)
               VALUES (?, ?, ?, ?)""",
            (session_id, fingerprint, event, ts),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
