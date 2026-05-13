"""SQLite persistence for python-telegram-bot.

Stores user_data, chat_data, and conversation states.
Provides multi-user support with database-backed durability.
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from telegram.ext import BasePersistence, PersistenceInput


class SQLitePersistence(BasePersistence):
    """SQLite-backed persistence for multi-user Telegram bots.

    Stores:
      - user_data: per-user key-value (email, undo_store, etc.)
      - chat_data: per-chat key-value (pending_file, fingerprint, etc.)
      - bot_data: global key-value
      - conversations: conversation state per (name, key) tuple
    """

    def __init__(
        self,
        db_path: str = "data/bot.db",
        store_data: PersistenceInput | None = None,
    ):
        super().__init__(
            store_data=store_data or PersistenceInput(
                user_data=True,
                chat_data=True,
                bot_data=True,
            )
        )
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_data (
                    user_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_data (
                    chat_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_data (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    name TEXT NOT NULL,
                    key BLOB NOT NULL,
                    state INTEGER,
                    PRIMARY KEY (name, key)
                );
            """)

    def _serialize(self, obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    def _deserialize(self, raw: str | None) -> Any:
        if raw is None:
            return None
        return json.loads(raw)

    # ── user_data ──────────────────────────────

    async def get_user_data(self) -> dict[int, dict]:
        data: dict[int, dict] = defaultdict(dict)
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute("SELECT user_id, data FROM user_data").fetchall()
            for uid, raw in rows:
                parsed = self._deserialize(raw)
                if isinstance(parsed, dict):
                    data[uid] = parsed
        return dict(data)

    async def update_user_data(self, user_id: int, data: dict) -> None:
        raw = self._serialize(data)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO user_data (user_id, data) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
                (user_id, raw),
            )

    async def refresh_user_data(self, user_id: int, user_data: dict) -> None:
        """Drop then re-insert (for complete overwrite)."""
        raw = self._serialize(user_data)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM user_data WHERE user_id = ?", (user_id,))
            conn.execute("INSERT INTO user_data (user_id, data) VALUES (?, ?)", (user_id, raw))

    async def drop_user_data(self, user_id: int) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM user_data WHERE user_id = ?", (user_id,))

    # ── chat_data ──────────────────────────────

    async def get_chat_data(self) -> dict[int, dict]:
        data: dict[int, dict] = defaultdict(dict)
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute("SELECT chat_id, data FROM chat_data").fetchall()
            for cid, raw in rows:
                parsed = self._deserialize(raw)
                if isinstance(parsed, dict):
                    data[cid] = parsed
        return dict(data)

    async def update_chat_data(self, chat_id: int, data: dict) -> None:
        raw = self._serialize(data)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO chat_data (chat_id, data) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET data = excluded.data",
                (chat_id, raw),
            )

    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        raw = self._serialize(chat_data)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM chat_data WHERE chat_id = ?", (chat_id,))
            conn.execute("INSERT INTO chat_data (chat_id, data) VALUES (?, ?)", (chat_id, raw))

    async def drop_chat_data(self, chat_id: int) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM chat_data WHERE chat_id = ?", (chat_id,))

    # ── bot_data ───────────────────────────────

    async def get_bot_data(self) -> dict:
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute("SELECT key, data FROM bot_data").fetchall()
            data = {}
            for key, raw in rows:
                parsed = self._deserialize(raw)
                if parsed is not None:
                    data[key] = parsed
            return data

    async def update_bot_data(self, data: dict) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            for key, value in data.items():
                raw = self._serialize(value)
                conn.execute(
                    "INSERT INTO bot_data (key, data) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                    (key, raw),
                )

    async def refresh_bot_data(self, data: dict) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM bot_data")
            for key, value in data.items():
                raw = self._serialize(value)
                conn.execute("INSERT INTO bot_data (key, data) VALUES (?, ?)", (key, raw))

    async def drop_bot_data(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM bot_data")

    # ── conversations ──────────────────────────

    async def get_conversations(self, name: str) -> dict[tuple, Any]:
        data: dict[tuple, Any] = {}
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT key, state FROM conversations WHERE name = ?", (name,)
            ).fetchall()
            for key_blob, state in rows:
                key = tuple(json.loads(key_blob.decode("utf-8")))
                data[key] = state
        return data

    async def update_conversation(self, name: str, key: tuple, new_state: Any) -> None:
        key_blob = json.dumps(key, ensure_ascii=False).encode("utf-8")
        with sqlite3.connect(str(self._db_path)) as conn:
            if new_state is None:
                conn.execute(
                    "DELETE FROM conversations WHERE name = ? AND key = ?",
                    (name, key_blob),
                )
            else:
                conn.execute(
                    "INSERT INTO conversations (name, key, state) VALUES (?, ?, ?) "
                    "ON CONFLICT(name, key) DO UPDATE SET state = excluded.state",
                    (name, key_blob, new_state),
                )

    async def flush(self) -> None:
        """SQLite writes are synchronous, no-op."""
        pass

    # ── callback_data (required by abstract base) ──

    async def get_callback_data(self) -> Any | None:
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute("SELECT data FROM bot_data WHERE key = '__callback'").fetchone()
            if row:
                parsed = self._deserialize(row[0])
                return parsed if isinstance(parsed, list) else None
            return None

    async def update_callback_data(self, data: list) -> None:
        raw = self._serialize(data)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO bot_data (key, data) VALUES ('__callback', ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (raw,),
            )
