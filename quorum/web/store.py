"""SQLite 存储：自定义模型、会话、参与者、发言。

设计取舍：
- 连接按操作创建（WAL 模式），避免多线程共享 connection 的问题；
- 所有写入都带 session_id，删除会话时级联清理；
- 自定义模型的 api_key 只存本地库（~/.quorum/web.db），不回传给前端。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_models (
    key         TEXT PRIMARY KEY,      -- provider:model
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key     TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hidden_models (
    key         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    rounds      INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- running | done | stopped | error
    judge_json  TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    winner      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS participants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    side        TEXT NOT NULL,          -- pro | con
    name        TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    base_url    TEXT NOT NULL DEFAULT '',
    api_key     TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    effort      TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    round          INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    participant_id INTEGER NOT NULL DEFAULT 0,
    side           TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL DEFAULT '',
    tokens         INTEGER NOT NULL DEFAULT 0,
    elapsed        REAL NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_id, side, order_index);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
"""


def default_db_path() -> Path:
    raw = os.environ.get("QUORUM_DB") or "~/.quorum/web.db"
    return Path(raw).expanduser()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ 自定义模型

    def list_custom_models(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM custom_models ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def add_custom_model(
        self, provider: str, model: str, base_url: str, api_key: str, label: str
    ) -> dict[str, Any]:
        key = f"{provider}:{model}"
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO custom_models(key, provider, model, base_url, api_key, label, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     base_url=excluded.base_url, api_key=excluded.api_key, label=excluded.label""",
                (key, provider, model, base_url.rstrip("/"), api_key, label, _now()),
            )
        return {
            "key": key,
            "provider": provider,
            "model": model,
            "base_url": base_url.rstrip("/"),
            "label": label,
            "custom": True,
        }

    def delete_custom_model(self, key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM custom_models WHERE key = ?", (key,))
            return cur.rowcount > 0

    def get_custom_model(self, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM custom_models WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 隐藏模型

    def hidden_keys(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key FROM hidden_models").fetchall()
        return {r["key"] for r in rows}

    def list_hidden_models(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, created_at FROM hidden_models ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def hide_model(self, key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO hidden_models(key, created_at) VALUES(?, ?)",
                (key, _now()),
            )
            return cur.rowcount > 0

    def unhide_model(self, key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM hidden_models WHERE key = ?", (key,))
            return cur.rowcount > 0

    # ------------------------------------------------------------ 会话

    def create_session(
        self,
        topic: str,
        rounds: int,
        participants: list[dict[str, Any]],
        judge: dict[str, Any] | None,
    ) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions(id, topic, rounds, status, judge_json, created_at)
                   VALUES(?, ?, ?, 'running', ?, ?)""",
                (sid, topic, rounds, json.dumps(judge or {}, ensure_ascii=False), _now()),
            )
            for idx, p in enumerate(participants):
                conn.execute(
                    """INSERT INTO participants
                       (session_id, side, name, provider, model, base_url, api_key, prompt, effort, order_index)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sid,
                        p["side"],
                        p["name"],
                        p["provider"],
                        p["model"],
                        p.get("base_url", ""),
                        p.get("api_key", ""),
                        p.get("prompt", ""),
                        p.get("effort", ""),
                        idx,
                    ),
                )
        return sid

    def add_message(self, sid: str, payload: dict[str, Any]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO messages
                   (session_id, round, seq, participant_id, side, name, model,
                    content, tokens, elapsed, error, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    payload.get("round", 0),
                    payload.get("seq", 0),
                    payload.get("participant_id", 0),
                    payload.get("side", ""),
                    payload.get("name", ""),
                    payload.get("model", ""),
                    payload.get("content", ""),
                    payload.get("tokens", 0),
                    payload.get("elapsed", 0.0),
                    payload.get("error", ""),
                    _now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def finish_session(
        self, sid: str, status: str, summary: str = "", winner: str = ""
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET status=?, summary=?, winner=?, finished_at=? WHERE id=?",
                (status, summary, winner, _now(), sid),
            )

    def mark_orphans(self) -> int:
        """进程重启后，上一次残留的 running 会话已无人驱动，标记为 error。"""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE sessions SET status='error', finished_at=? WHERE status='running'",
                (_now(),),
            )
            return cur.rowcount

    def list_sessions(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                          (SELECT GROUP_CONCAT(p.name, ' vs ') FROM participants p
                             WHERE p.session_id = s.id AND p.side = 'pro') AS pro_names,
                          (SELECT GROUP_CONCAT(p.name, ' vs ') FROM participants p
                             WHERE p.session_id = s.id AND p.side = 'con') AS con_names
                   FROM sessions s ORDER BY s.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item.pop("judge_json", None)
            out.append(item)
        return out

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            if session is None:
                return None
            parts = conn.execute(
                "SELECT * FROM participants WHERE session_id = ? ORDER BY order_index", (sid,)
            ).fetchall()
            msgs = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (sid,)
            ).fetchall()

        data = dict(session)
        judge_raw = data.pop("judge_json", "") or ""
        try:
            judge = json.loads(judge_raw) if judge_raw else {}
        except json.JSONDecodeError:
            judge = {}
        # 不把 api_key 回传前端
        participants = []
        for row in parts:
            p = dict(row)
            p.pop("api_key", None)
            participants.append(p)
        data["judge"] = judge
        data["participants"] = participants
        data["messages"] = [dict(m) for m in msgs]
        return data

    def delete_session(self, sid: str) -> bool:
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM participants WHERE session_id = ?", (sid,))
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            return cur.rowcount > 0
