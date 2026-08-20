"""SQLite state for deduplication, users, and Instagram group moderation."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config
from lib.memory_engine import (
    EmbeddingEngine,
    HybridRanker,
    MemoryDecay,
    sqlite_cosine_blob,
)
from settings import DATABASE_PATH


class Database:
    def __init__(self, path: Path = DATABASE_PATH, memory_dir: Path | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        default_memory_dir = Path.home() / "Desktop" / "memeory"
        if self.path.resolve() != Path(DATABASE_PATH).resolve():
            default_memory_dir = self.path.parent / "memeory"
        self.memory_dir = Path(memory_dir) if memory_dir is not None else default_memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.chmod(0o700)
        self.memory_lock = threading.Lock()
        self.embedding_engine = EmbeddingEngine()
        self._initialize()
        self.export_all_ai_memory()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Each worker gets a short-lived connection. WAL lets readers continue
        # while another worker commits, and busy_timeout absorbs brief bursts.
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.create_function("COSINE_SIM", 2, sqlite_cosine_blob)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS ai_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_ai_memory_user ON ai_memory(user_id, id DESC);
                CREATE TABLE IF NOT EXISTS ai_friends (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    befriended_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS ai_user_facts (
                    user_id TEXT NOT NULL,
                    fact_type TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, fact_type, fact_key)
                );
                CREATE TABLE IF NOT EXISTS ai_user_topics (
                    user_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    mentions INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, topic)
                );
                CREATE TABLE IF NOT EXISTS ai_thread_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_ai_thread_context ON ai_thread_context(thread_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_thread_context_user ON ai_thread_context(user_id, id DESC);

                CREATE TABLE IF NOT EXISTS ai_working_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_working_session ON ai_working_memory(session_key, id DESC);

                CREATE TABLE IF NOT EXISTS ai_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    mood TEXT NOT NULL DEFAULT 'neutral',
                    significance INTEGER NOT NULL DEFAULT 5,
                    valence REAL NOT NULL DEFAULT 0.0,
                    is_milestone INTEGER NOT NULL DEFAULT 0,
                    milestone_type TEXT,
                    embedding BLOB,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_recalled_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_user_rapport (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    rapport_score INTEGER NOT NULL DEFAULT 50,
                    grudge_strikes INTEGER NOT NULL DEFAULT 0,
                    violation_count INTEGER NOT NULL DEFAULT 0,
                    current_mood TEXT NOT NULL DEFAULT 'chill',
                    inside_jokes TEXT NOT NULL DEFAULT '[]',
                    last_seen REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_rapport_score ON ai_user_rapport(rapport_score);

                CREATE TABLE IF NOT EXISTS ai_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_username TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    target TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_audit_actor ON ai_audit_log(actor_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS ai_chat_mood (
                    thread_id TEXT PRIMARY KEY,
                    current_mood TEXT NOT NULL DEFAULT 'chill',
                    aggression_level INTEGER NOT NULL DEFAULT 0,
                    last_provocation_ts REAL NOT NULL DEFAULT 0.0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS thread_settings (
                    thread_id TEXT PRIMARY KEY,
                    antilink INTEGER NOT NULL DEFAULT 0,
                    antibadword INTEGER NOT NULL DEFAULT 0,
                    antispam INTEGER NOT NULL DEFAULT 0,
                    bot_muted INTEGER NOT NULL DEFAULT 0,
                    admin_only INTEGER NOT NULL DEFAULT 0,
                    ai_auto_reply INTEGER NOT NULL DEFAULT 0,
                    ai_auto_reply_vn INTEGER NOT NULL DEFAULT 0,
                    gc_monitor INTEGER NOT NULL DEFAULT 0,
                    gc_monitor_admin_id TEXT,
                    tts_enabled INTEGER NOT NULL DEFAULT 1,
                    max_warnings INTEGER NOT NULL DEFAULT 3,
                    rules TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS thread_user_messages (
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(thread_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS warnings (
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(thread_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS banned_users (
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reason TEXT,
                    PRIMARY KEY(thread_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS gc_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    offender_id TEXT NOT NULL,
                    offender_username TEXT NOT NULL,
                    rule_broken TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_gc_reports_thread ON gc_reports(thread_id, status);
                """
            )
            # Safe schema migrations for existing SQLite databases
            for migration in (
                "ALTER TABLE thread_settings ADD COLUMN ai_auto_reply_vn INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE thread_settings ADD COLUMN gc_monitor INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE thread_settings ADD COLUMN gc_monitor_admin_id TEXT",
                "ALTER TABLE thread_settings ADD COLUMN tts_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE thread_settings ADD COLUMN botgf_enabled INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE thread_settings ADD COLUMN botgf_target TEXT",
                "ALTER TABLE banned_users ADD COLUMN banned_by TEXT NOT NULL DEFAULT 'admin'",
            ):
                try:
                    connection.execute(migration)
                except sqlite3.OperationalError:
                    pass
            try:
                connection.execute("DELETE FROM banned_users WHERE reason LIKE 'Automatic moderation:%'")
            except sqlite3.OperationalError:
                pass

    @staticmethod
    def _memory_filename(username: str, user_id: str = "") -> str:
        clean_user = re.sub(r"[^a-zA-Z0-9._-]+", "_", username.lstrip("@")).strip("_") or "user"
        return f"{clean_user}.json"

    @staticmethod
    def _rapport(message_count: int, owner: bool, friend: bool) -> str:
        if owner:
            return "loyal_owner"
        if friend:
            return "inner_circle"
        if message_count >= 100:
            return "best_friend"
        if message_count >= 30:
            return "close_friend"
        if message_count >= 10:
            return "friendly"
        if message_count >= 3:
            return "acquaintance"
        return "stranger"

    def _learn_from_message(self, connection: sqlite3.Connection, user_id: str, message: str) -> None:
        text = " ".join(message.strip().split())
        if not text or len(text) < 3 or text.startswith((".", "/", "!", "$", "#")):
            return
        lowered = text.lower()
        patterns = (
            ("nickname", "nickname", r"\b(?:call me|my name is)\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("preferred_address", "preferred_address", r"\b(?:call me|address me as|call me ur|im ur|i am your)\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("likes", "like", r"\bi\s+(?:really\s+)?(?:like|love|enjoy)\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("dislikes", "dislike", r"\bi\s+(?:really\s+)?(?:hate|dislike|cant\s+stand|can\'t\s+stand)\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("plays", "game", r"\bi\s+(?:play|main)\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "game", r"\bmy\s+fav(?:orite)?\s+game\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "color", r"\bmy\s+fav(?:orite)?\s+color\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "food", r"\bmy\s+fav(?:orite)?\s+food\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "anime", r"\bmy\s+fav(?:orite)?\s+anime\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "song", r"\bmy\s+fav(?:orite)?\s+(?:song|track)\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
            ("favorites", "artist", r"\bmy\s+fav(?:orite)?\s+(?:artist|singer|band)\s+is\s+([a-zA-Z0-9_\- ]{2,40})"),
        )
        if "twin" in lowered and not any(p in lowered for p in ("twin turbo", "twin bed")):
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), "preferred_address", "preferred_address", "twin"),
            )
        for fact_type, key, pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            raw_value = match.group(1).strip(" .,!?:;")
            cleaned = re.split(r"\b(?:and|but|because|when|while|though|although|so)\b", raw_value)[0].strip()
            if len(cleaned) < 2 or len(cleaned) > 35:
                continue
            fact_key = cleaned if fact_type in {"likes", "dislikes", "plays"} else key
            fact_val = cleaned
            if fact_type in {"nickname", "preferred_address"}:
                fact_type_db = "facts"
                fact_key_db = fact_type
            else:
                fact_type_db = fact_type
                fact_key_db = fact_key
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), fact_type_db, fact_key_db, fact_val),
            )
        tokens = set(re.findall(r"\b\w+\b", lowered))
        topic_words = {
            "gaming": ("game", "games", "gaming", "valorant", "genshin", "minecraft", "roblox", "fortnite", "steam"),
            "valorant": ("valorant", "valo"),
            "genshin": ("genshin",),
            "anime": ("anime", "manga", "weeb", "otaku", "naruto", "jojos", "jojo", "onepiece"),
            "music": ("music", "song", "songs", "spotify", "album", "band", "singer"),
            "coding": ("code", "coding", "python", "github"),
            "memes": ("meme", "memes", "lol", "lmao", "😂", "😭", "💀", "☠"),
            "relationships": ("gf", "girlfriend", "boyfriend", "crush", "love"),
            "technology": ("wifi", "phone", "computer", "internet"),
        }
        for topic, words in topic_words.items():
            if any(word in tokens or word in lowered for word in words):
                connection.execute(
                    """INSERT INTO ai_user_topics(user_id, topic) VALUES (?, ?)
                       ON CONFLICT(user_id, topic) DO UPDATE SET
                         mentions=mentions+1, updated_at=CURRENT_TIMESTAMP""",
                    (str(user_id), topic),
                )

    def _sync_ai_memory_file(self, user_id: str, username: str) -> None:
        if not username:
            return
        with self.memory_lock:
            with self._connect() as connection:
                user = connection.execute(
                    """SELECT message_count, first_seen_at, updated_at FROM users WHERE user_id = ?""",
                    (str(user_id),),
                ).fetchone()
                rows = connection.execute(
                    """SELECT user_message, assistant_message, created_at FROM ai_memory
                       WHERE user_id = ? ORDER BY id DESC LIMIT 12""",
                    (str(user_id),),
                ).fetchall()
                recent = connection.execute(
                    """SELECT message, created_at FROM ai_thread_context WHERE user_id = ?
                       ORDER BY id DESC LIMIT 20""",
                    (str(user_id),),
                ).fetchall()
                fact_rows = connection.execute(
                    """SELECT fact_type, fact_key, fact_value, updated_at FROM ai_user_facts
                       WHERE user_id = ? ORDER BY fact_type, fact_key""",
                    (str(user_id),),
                ).fetchall()
                topics = connection.execute(
                    """SELECT topic, mentions FROM ai_user_topics WHERE user_id = ?
                       ORDER BY mentions DESC, topic LIMIT 10""",
                    (str(user_id),),
                ).fetchall()
                friend = connection.execute(
                    "SELECT 1 FROM ai_friends WHERE user_id = ?", (str(user_id),)
                ).fetchone() is not None
            message_count = int(user["message_count"]) if user else 0
            owner = config.is_owner(username, str(user_id))
            facts: dict[str, object] = {}
            for row in fact_rows:
                fact_type, key, value = str(row["fact_type"]), str(row["fact_key"]), str(row["fact_value"])
                if fact_type in {"likes", "dislikes", "plays"}:
                    facts.setdefault(fact_type, []).append(value)
                elif fact_type == "favorites":
                    facts.setdefault("favorites", {})[key] = value
                elif fact_type == "taught":
                    facts.setdefault("taught", {})[key] = value
                else:
                    facts[key or fact_type] = value
            payload = {
                "schema_version": 2,
                "identity": {"user_id": str(user_id), "username": username.lstrip("@")},
                "relationship": {
                    "role": "owner" if owner else "protected_friend" if friend else "member",
                    "rapport": self._rapport(message_count, owner, friend),
                    "protected_friend": friend,
                },
                "profile": {
                    "facts": facts,
                    "interests": [{"topic": str(row["topic"]), "mentions": int(row["mentions"])} for row in topics],
                },
                "activity": {
                    "messages_seen": message_count,
                    "first_seen": str(user["first_seen_at"] or "") if user else "",
                    "last_seen": str(user["updated_at"] or "") if user else "",
                    "saved_exchanges": len(rows),
                },
                "recent_messages": [
                    {"text": str(row["message"]), "at": str(row["created_at"])} for row in reversed(recent)
                ],
                "recent_exchanges": [
                    {"user": str(row["user_message"]), "ineffa": str(row["assistant_message"]), "at": str(row["created_at"])}
                    for row in reversed(rows)
                ],
            }
            destination = self.memory_dir / self._memory_filename(username, user_id)
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(destination)

    def export_all_ai_memory(self) -> None:
        with self._connect() as connection:
            users = connection.execute(
                "SELECT user_id, COALESCE(username, '') AS username FROM users WHERE username IS NOT NULL"
            ).fetchall()
        for user in users:
            self._sync_ai_memory_file(str(user["user_id"]), str(user["username"]))

    def rebuild_ai_profiles(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM ai_user_facts WHERE fact_type != 'taught'")
            connection.execute("DELETE FROM ai_user_topics")
            rows = connection.execute(
                """SELECT user_id, message FROM ai_thread_context
                   UNION SELECT user_id, user_message AS message FROM ai_memory"""
            ).fetchall()
            seen: set[tuple[str, str]] = set()
            for row in rows:
                item = (str(row["user_id"]), str(row["message"]))
                if item in seen:
                    continue
                seen.add(item)
                self._learn_from_message(connection, item[0], item[1])
        self.export_all_ai_memory()

    def claim_message(self, message_id: str, thread_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO processed_messages(message_id, thread_id) VALUES (?, ?)",
                (str(message_id), str(thread_id)),
            )
            if cursor.rowcount == 1:
                return True
            cursor = connection.execute(
                """UPDATE processed_messages SET processed_at = CURRENT_TIMESTAMP
                   WHERE message_id = ? AND completed_at IS NULL
                     AND processed_at < datetime('now', '-5 minutes')""",
                (str(message_id),),
            )
            return cursor.rowcount == 1

    def complete_message(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE processed_messages SET completed_at = CURRENT_TIMESTAMP WHERE message_id = ?",
                (str(message_id),),
            )

    def unclaim_message(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM processed_messages WHERE message_id = ? AND completed_at IS NULL",
                (str(message_id),),
            )

    def record_user_message(self, user_id: str, username: str | None, message: str = "", thread_id: str | None = None) -> None:
        clean_user = username.lstrip("@") if username else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users(user_id, username, message_count) VALUES (?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    message_count = users.message_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(user_id), clean_user),
            )
            connection.execute(
                "UPDATE users SET first_seen_at = COALESCE(first_seen_at, CURRENT_TIMESTAMP) WHERE user_id = ?",
                (str(user_id),),
            )
            if thread_id:
                connection.execute(
                    """
                    INSERT INTO thread_user_messages(thread_id, user_id, username, message_count) VALUES (?, ?, ?, 1)
                    ON CONFLICT(thread_id, user_id) DO UPDATE SET
                        username = COALESCE(excluded.username, thread_user_messages.username),
                        message_count = thread_user_messages.message_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (str(thread_id), str(user_id), clean_user),
                )
            self._learn_from_message(connection, str(user_id), message)
        if clean_user:
            self._sync_ai_memory_file(str(user_id), clean_user)

    def ai_profile_context(self, user_id: str) -> str:
        with self._connect() as connection:
            facts = connection.execute(
                "SELECT fact_type, fact_key, fact_value FROM ai_user_facts WHERE user_id = ? ORDER BY updated_at DESC LIMIT 12",
                (str(user_id),),
            ).fetchall()
            topics = connection.execute(
                "SELECT topic FROM ai_user_topics WHERE user_id = ? ORDER BY mentions DESC LIMIT 5",
                (str(user_id),),
            ).fetchall()
        details = []
        for row in facts:
            ft, fk, fv = str(row["fact_type"]), str(row["fact_key"]), str(row["fact_value"])
            if ft == "taught":
                details.append(f"{fk}: {fv}")
            elif fk == fv or ft in {"likes", "dislikes", "plays"}:
                details.append(f"{ft} {fv}")
            else:
                details.append(f"{ft} {fk}: {fv}")
        if topics:
            details.append("interests: " + ", ".join(str(row["topic"]) for row in topics))
        return "; ".join(details)[:600]

    def remember_ai_exchange(self, user_id: str, username: str, prompt: str, response: str) -> None:
        clean_user = str(username or "")[:100]
        try:
            self.append_working_turn(f"dm_{user_id}", user_id, clean_user, "user", prompt)
            self.append_working_turn(f"dm_{user_id}", user_id, clean_user, "assistant", response)
        except Exception:
            pass
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO ai_memory(user_id, username, user_message, assistant_message) VALUES (?, ?, ?, ?)",
                (str(user_id), clean_user, prompt[:1000], response[:1000]),
            )
            connection.execute(
                """DELETE FROM ai_memory WHERE user_id = ? AND id NOT IN
                   (SELECT id FROM ai_memory WHERE user_id = ? ORDER BY id DESC LIMIT 20)""",
                (str(user_id), str(user_id)),
            )
            connection.execute(
                """
                INSERT INTO users(user_id, username, message_count) VALUES (?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(user_id), clean_user),
            )
        if clean_user:
            self._sync_ai_memory_file(str(user_id), clean_user)

    def recent_ai_exchanges(self, user_id: str, limit: int = 4) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT user_message, assistant_message FROM ai_memory
                   WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
                (str(user_id), min(20, max(1, int(limit)))),
            ).fetchall()
        return [(str(row["user_message"]), str(row["assistant_message"])) for row in reversed(rows)]

    def ai_history(self, user_id: str, limit: int = 20) -> list[tuple[str, str]]:
        return self.recent_ai_exchanges(user_id, limit=limit)

    def remember_thread_message(self, thread_id: str, user_id: str, username: str, message: str) -> None:
        clean_user = str(username or "")[:100]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO ai_thread_context(thread_id, user_id, username, message) VALUES (?, ?, ?, ?)",
                (str(thread_id), str(user_id), clean_user, message[:600]),
            )
            connection.execute(
                """DELETE FROM ai_thread_context WHERE thread_id = ? AND id NOT IN
                   (SELECT id FROM ai_thread_context WHERE thread_id = ? ORDER BY id DESC LIMIT 25)""",
                (str(thread_id), str(thread_id)),
            )

    def recent_thread_messages(self, thread_id: str, limit: int = 6) -> list[tuple[str, str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT user_id, username, message FROM ai_thread_context
                   WHERE thread_id = ? ORDER BY id DESC LIMIT ?""",
                (str(thread_id), min(15, max(1, int(limit)))),
            ).fetchall()
        return [(str(row["user_id"]), str(row["username"]), str(row["message"])) for row in reversed(rows)]

    def ai_thread_history(self, thread_id: str, limit: int = 10) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT username, message FROM ai_thread_context
                   WHERE thread_id = ? ORDER BY id DESC LIMIT ?""",
                (str(thread_id), min(50, max(1, int(limit)))),
            ).fetchall()
        return [(str(row["username"]), str(row["message"])) for row in reversed(rows)]

    def mark_ai_friend(self, user_id: str, username: str) -> None:
        clean_user = str(username or "").lstrip("@")[:100]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_friends(user_id, username) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET username = excluded.username""",
                (str(user_id), clean_user),
            )
        if clean_user:
            self._sync_ai_memory_file(str(user_id), clean_user)

    def unmark_ai_friend(self, user_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM ai_friends WHERE user_id = ?", (str(user_id),))
            deleted = cursor.rowcount > 0
        return deleted

    def is_ai_friend(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM ai_friends WHERE user_id = ?", (str(user_id),)).fetchone()
        return row is not None

    def ai_friend_usernames(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT username FROM ai_friends").fetchall()
        return {str(row["username"]).lower().lstrip("@") for row in rows}

    def bot_setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?", (str(key),)
            ).fetchone()
        return str(row["setting_value"]) if row else None

    def set_bot_setting(self, key: str, value: str) -> None:
        if key not in {"ai_auto_reply_dm", "ai_auto_reply_vn_dm", "tts_global_enabled"}:
            raise ValueError(f"Unknown bot setting: {key}")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO bot_settings(setting_key, setting_value) VALUES (?, ?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP""",
                (str(key), str(value)),
            )

    def thread_settings(self, thread_id: str) -> dict[str, bool | int | str]:
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            row = connection.execute("SELECT * FROM thread_settings WHERE thread_id = ?", (str(thread_id),)).fetchone()
        return {
            "antilink": bool(row["antilink"]), "antibadword": bool(row["antibadword"]),
            "antispam": bool(row["antispam"]),
            "bot_muted": bool(row["bot_muted"]), "admin_only": bool(row["admin_only"]),
            "ai_auto_reply": bool(row["ai_auto_reply"]),
            "ai_auto_reply_vn": bool(row["ai_auto_reply_vn"]),
            "gc_monitor": bool(row["gc_monitor"]) if "gc_monitor" in row.keys() else False,
            "gc_monitor_admin_id": str(row["gc_monitor_admin_id"] or "") if "gc_monitor_admin_id" in row.keys() else "",
            "tts_enabled": bool(row["tts_enabled"]) if "tts_enabled" in row.keys() else True,
            "botgf_enabled": bool(row["botgf_enabled"]) if "botgf_enabled" in row.keys() else False,
            "botgf_target": str(row["botgf_target"] or "") if "botgf_target" in row.keys() else "",
            "max_warnings": int(row["max_warnings"]), "rules": str(row["rules"] or ""),
        }

    def set_botgf(self, thread_id: str, target_username: str, enabled: bool) -> None:
        clean_target = str(target_username).strip().lstrip("@").lower()
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute(
                "UPDATE thread_settings SET botgf_enabled = ?, botgf_target = ? WHERE thread_id = ?",
                (int(enabled), clean_target if enabled else "", str(thread_id))
            )

    def set_thread_flag(self, thread_id: str, flag: str, enabled: bool, admin_id: str | None = None) -> None:
        if flag not in {"antilink", "antibadword", "antispam", "bot_muted", "admin_only", "ai_auto_reply", "ai_auto_reply_vn", "gc_monitor", "tts_enabled", "botgf_enabled"}:
            raise ValueError(f"Unknown thread setting: {flag}")
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute(f"UPDATE thread_settings SET {flag} = ? WHERE thread_id = ?", (int(enabled), str(thread_id)))
            if flag == "gc_monitor" and enabled and admin_id:
                connection.execute("UPDATE thread_settings SET gc_monitor_admin_id = ? WHERE thread_id = ?", (str(admin_id), str(thread_id)))

    def set_max_warnings(self, thread_id: str, maximum: int) -> None:
        maximum = min(10, max(1, int(maximum)))
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute("UPDATE thread_settings SET max_warnings = ? WHERE thread_id = ?", (maximum, str(thread_id)))

    def set_thread_rules(self, thread_id: str, rules: str) -> None:
        rules = " ".join(str(rules).split())[:1000]
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute("UPDATE thread_settings SET rules = ? WHERE thread_id = ?", (rules, str(thread_id)))

    def add_warning(self, thread_id: str, user_id: str) -> int:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO warnings(thread_id, user_id, count) VALUES (?, ?, 1)
                ON CONFLICT(thread_id, user_id) DO UPDATE SET count = count + 1""",
                (str(thread_id), str(user_id)),
            )
            row = connection.execute("SELECT count FROM warnings WHERE thread_id = ? AND user_id = ?", (str(thread_id), str(user_id))).fetchone()
        return int(row["count"])

    def warning_count(self, thread_id: str, user_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT count FROM warnings WHERE thread_id = ? AND user_id = ?", (str(thread_id), str(user_id))).fetchone()
        return int(row["count"]) if row else 0

    def clear_warnings(self, thread_id: str, user_id: str) -> int:
        previous = self.warning_count(thread_id, user_id)
        with self._connect() as connection:
            connection.execute("DELETE FROM warnings WHERE thread_id = ? AND user_id = ?", (str(thread_id), str(user_id)))
        return previous

    def warning_list(self, thread_id: str, limit: int = 20) -> list[tuple[str, str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT w.user_id, COALESCE(u.username, w.user_id) AS username, w.count
                   FROM warnings w LEFT JOIN users u ON u.user_id = w.user_id
                   WHERE w.thread_id = ? AND w.count > 0
                   ORDER BY w.count DESC LIMIT ?""",
                (str(thread_id), min(50, max(1, int(limit)))),
            ).fetchall()
        return [(str(row["user_id"]), str(row["username"]), int(row["count"])) for row in rows]

    def ban_user(self, thread_id: str, user_id: str, reason: str = "", banned_by: str = "admin") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO banned_users(thread_id, user_id, reason, banned_by) VALUES (?, ?, ?, ?)",
                (str(thread_id), str(user_id), reason[:300], str(banned_by)),
            )

    def unban_user(self, thread_id: str, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM banned_users WHERE (thread_id = ? OR thread_id = 'global') AND (user_id = ? OR LOWER(user_id) = ?)", (str(thread_id), str(user_id), str(user_id).lower().lstrip("@")))

    def get_ban_info(self, thread_id: str, user_id: str, username: str = "") -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id, user_id, reason, COALESCE(banned_by, 'admin') AS banned_by FROM banned_users WHERE (thread_id = ? OR thread_id = 'global') AND user_id = ?",
                (str(thread_id), str(user_id)),
            ).fetchone()
            if row is not None:
                return dict(row)
            if username:
                clean_name = username.lower().lstrip("@")
                row_user = connection.execute(
                    "SELECT thread_id, user_id, reason, COALESCE(banned_by, 'admin') AS banned_by FROM banned_users WHERE (thread_id = ? OR thread_id = 'global') AND LOWER(user_id) = ?",
                    (str(thread_id), clean_name),
                ).fetchone()
                if row_user is not None:
                    return dict(row_user)
        return None

    def is_banned(self, thread_id: str, user_id: str, username: str = "") -> bool:
        return self.get_ban_info(thread_id, user_id, username) is not None

    def ban_list(self, thread_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            if thread_id:
                rows = connection.execute(
                    """SELECT b.thread_id, b.user_id, COALESCE(u.username, b.user_id) AS username, b.reason, COALESCE(b.banned_by, 'admin') AS banned_by
                       FROM banned_users b LEFT JOIN users u ON u.user_id = b.user_id
                       WHERE b.thread_id = ? OR b.thread_id = 'global'
                       ORDER BY b.rowid DESC""",
                    (str(thread_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT b.thread_id, b.user_id, COALESCE(u.username, b.user_id) AS username, b.reason, COALESCE(b.banned_by, 'admin') AS banned_by
                       FROM banned_users b LEFT JOIN users u ON u.user_id = b.user_id
                       ORDER BY b.rowid DESC"""
                ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            messages = connection.execute("SELECT COUNT(*) FROM processed_messages").fetchone()[0]
            bans = connection.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
        return {"users": int(users), "messages": int(messages), "bans": int(bans)}

    def top_users(self, thread_id: str | None = None, limit: int = 10) -> list[tuple[str, int]]:
        with self._connect() as connection:
            if thread_id:
                rows = connection.execute(
                    """SELECT username, message_count FROM thread_user_messages
                       WHERE thread_id = ? AND username IS NOT NULL AND username != 'unknown' AND message_count > 0
                       ORDER BY message_count DESC LIMIT ?""",
                    (str(thread_id), min(50, max(1, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT username, message_count FROM users
                       WHERE username IS NOT NULL AND username != 'unknown' AND message_count > 0
                       ORDER BY message_count DESC LIMIT ?""",
                    (min(50, max(1, int(limit))),),
                ).fetchall()
        return [(str(row["username"]), int(row["message_count"])) for row in rows]

    def teach_fact(self, user_id: str, key: str, value: str) -> None:
        key_clean = re.sub(r"[^a-z0-9_-]+", "_", key.strip().lower())[:40] or "custom_fact"
        val_clean = " ".join(value.strip().split())[:200]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, 'taught', ?, ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), key_clean, val_clean),
            )
            user_row = connection.execute("SELECT username FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        if user_row and user_row["username"]:
            self._sync_ai_memory_file(str(user_id), str(user_row["username"]))

    def get_user_facts(self, user_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fact_key, fact_value FROM ai_user_facts WHERE user_id = ? ORDER BY updated_at DESC LIMIT 15",
                (str(user_id),),
            ).fetchall()
        return {str(row["fact_key"]): str(row["fact_value"]) for row in rows}

    def add_report(self, thread_id: str, offender_id: str, offender_username: str, rule_broken: str, reason: str, snippet: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO gc_reports(thread_id, offender_id, offender_username, rule_broken, reason, snippet, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (str(thread_id), str(offender_id), str(offender_username), str(rule_broken), str(reason), str(snippet)[:300]),
            )
            return int(cursor.lastrowid)

    def get_pending_reports(self, thread_id: str | None = None, limit: int = 10) -> list[dict[str, object]]:
        with self._connect() as connection:
            if thread_id:
                rows = connection.execute(
                    """SELECT id, thread_id, offender_id, offender_username, rule_broken, reason, snippet, status, created_at
                       FROM gc_reports WHERE thread_id = ? AND status = 'pending'
                       ORDER BY id DESC LIMIT ?""",
                    (str(thread_id), min(50, max(1, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT id, thread_id, offender_id, offender_username, rule_broken, reason, snippet, status, created_at
                       FROM gc_reports WHERE status = 'pending'
                       ORDER BY id DESC LIMIT ?""",
                    (min(50, max(1, int(limit))),),
                ).fetchall()
            return [dict(row) for row in rows]

    def resolve_report(self, report_id: int, status: str = "resolved") -> bool:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE gc_reports SET status = ? WHERE id = ?", (status, int(report_id)))
            return cursor.rowcount > 0

    def prune(self, days: int = 30) -> None:
        safe_days = abs(int(days))
        with self._connect() as connection:
            connection.execute("DELETE FROM processed_messages WHERE processed_at < datetime('now', ?)", (f"-{safe_days} days",))
            connection.execute("DELETE FROM ai_memory WHERE created_at < datetime('now', '-90 days')")
            connection.execute("DELETE FROM gc_reports WHERE status != 'pending' AND created_at < datetime('now', '-30 days')")

    # -------------------------------------------------------------------------
    # 3-Tier Hierarchical AI Memory Operations
    # -------------------------------------------------------------------------
    def append_working_turn(self, session_key: str, user_id: str, username: str, role: str, content: str) -> None:
        """Add conversation turn to active working memory buffer."""
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_working_memory(session_key, user_id, username, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(session_key), str(user_id), str(username or ""), str(role), str(content)[:1500], now),
            )
            # Retain only last 30 turns per active session in working memory
            connection.execute(
                """
                DELETE FROM ai_working_memory
                WHERE session_key = ? AND id NOT IN (
                    SELECT id FROM ai_working_memory WHERE session_key = ? ORDER BY id DESC LIMIT 30
                )
                """,
                (str(session_key), str(session_key)),
            )

    def get_working_memory(self, session_key: str, limit: int = 10) -> list[dict[str, object]]:
        """Retrieve recent working memory turns in chronological order."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, username, role, content, created_at
                FROM ai_working_memory
                WHERE session_key = ?
                ORDER BY id DESC LIMIT ?
                """,
                (str(session_key), max(1, min(50, limit))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def clear_working_memory(self, session_key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM ai_working_memory WHERE session_key = ?", (str(session_key),))

    def record_episode(
        self,
        user_id: str,
        session_key: str,
        summary: str,
        mood: str = "neutral",
        significance: int = 5,
        valence: float = 0.0,
        is_milestone: bool = False,
        milestone_type: str | None = None,
    ) -> int:
        """Persist a consolidated conversation episode with dense vector embedding."""
        now = time.time()
        summary_clean = " ".join(summary.strip().split())[:1200]
        embedding_vec = self.embedding_engine.embed_text(summary_clean)
        blob = self.embedding_engine.pack_vector(embedding_vec)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ai_episodes(
                    user_id, session_key, summary, mood, significance, valence,
                    is_milestone, milestone_type, embedding, recall_count, created_at, last_recalled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    str(user_id),
                    str(session_key),
                    summary_clean,
                    str(mood),
                    max(1, min(10, int(significance))),
                    max(-1.0, min(1.0, float(valence))),
                    1 if is_milestone else 0,
                    milestone_type,
                    blob,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def touch_episode_recall(self, episode_id: int) -> None:
        """Reinforce episodic memory by updating last_recalled_at and incrementing recall_count."""
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ai_episodes
                SET recall_count = recall_count + 1, last_recalled_at = ?
                WHERE id = ?
                """,
                (now, int(episode_id)),
            )

    def recall_relevant_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = 4,
    ) -> list[dict[str, object]]:
        """Perform hybrid similarity retrieval over Episodic memory with time decay."""
        query_clean = " ".join(query.strip().split())[:300]
        if not query_clean:
            return []

        query_vec = self.embedding_engine.embed_text(query_clean)
        query_blob = self.embedding_engine.pack_vector(query_vec)

        with self._connect() as connection:
            vec_rows = connection.execute(
                """
                SELECT id, summary, mood, significance, valence, is_milestone, created_at, last_recalled_at, recall_count,
                       COSINE_SIM(embedding, ?) AS sim
                FROM ai_episodes
                WHERE user_id = ? AND embedding IS NOT NULL
                ORDER BY sim DESC LIMIT 15
                """,
                (query_blob, str(user_id)),
            ).fetchall()

        vec_list: list[dict[str, object]] = []
        for r in vec_rows:
            d = dict(r)
            d["retention"] = MemoryDecay.calculate_retention(
                float(d["created_at"]), float(d["last_recalled_at"]), int(d["significance"]), int(d["recall_count"])
            )
            vec_list.append(d)

        fused = HybridRanker.fuse_results([], vec_list, k=60, alpha=0.0)
        for item in fused[:top_k]:
            self.touch_episode_recall(int(item["id"]))

        return fused[:top_k]

    def compact_and_decay(self, retention_threshold: float = 0.08) -> dict[str, int]:
        """Prune decayed memories and compact active working context."""
        now = time.time()
        pruned_episodes = 0
        pruned_working = 0

        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_working_memory WHERE created_at < ?", (now - (14 * 86400),)
            )
            pruned_working = cursor.rowcount

            episodes = connection.execute(
                "SELECT id, created_at, last_recalled_at, significance, recall_count FROM ai_episodes WHERE is_milestone = 0"
            ).fetchall()
            ids_to_prune = []
            for ep in episodes:
                retention = MemoryDecay.calculate_retention(
                    float(ep["created_at"]), float(ep["last_recalled_at"]), int(ep["significance"]), int(ep["recall_count"])
                )
                if retention < retention_threshold:
                    ids_to_prune.append(ep["id"])

            if ids_to_prune:
                placeholders = ",".join("?" * len(ids_to_prune))
                connection.execute(f"DELETE FROM ai_episodes WHERE id IN ({placeholders})", ids_to_prune)
                pruned_episodes = len(ids_to_prune)

        return {"pruned_episodes": pruned_episodes, "pruned_working": pruned_working}

    def get_user_rapport(self, user_id: str, username: str) -> dict[str, object]:
        """Fetch or initialize dynamic rapport and strike profile for a user."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, username, rapport_score, grudge_strikes, violation_count, current_mood, inside_jokes, last_seen FROM ai_user_rapport WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            if row:
                return dict(row)

            now = time.time()
            connection.execute(
                """
                INSERT INTO ai_user_rapport (user_id, username, rapport_score, grudge_strikes, violation_count, current_mood, inside_jokes, last_seen)
                VALUES (?, ?, 50, 0, 0, 'chill', '[]', ?)
                """,
                (str(user_id), username, now),
            )
            return {
                "user_id": str(user_id),
                "username": username,
                "rapport_score": 50,
                "grudge_strikes": 0,
                "violation_count": 0,
                "current_mood": "chill",
                "inside_jokes": "[]",
                "last_seen": now,
            }

    def update_user_rapport(
        self,
        user_id: str,
        username: str,
        delta_score: int = 0,
        delta_strikes: int = 0,
        mood: str | None = None,
    ) -> dict[str, object]:
        """Update a user's rapport score, strikes, and directed emotional state."""
        profile = self.get_user_rapport(user_id, username)
        new_score = max(0, min(100, int(profile["rapport_score"]) + delta_score))
        new_strikes = max(0, int(profile["grudge_strikes"]) + delta_strikes)
        new_mood = mood or str(profile["current_mood"])
        now = time.time()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ai_user_rapport
                SET username = ?, rapport_score = ?, grudge_strikes = ?, current_mood = ?, last_seen = ?
                WHERE user_id = ?
                """,
                (username, new_score, new_strikes, new_mood, now, str(user_id)),
            )

        profile["rapport_score"] = new_score
        profile["grudge_strikes"] = new_strikes
        profile["current_mood"] = new_mood
        profile["last_seen"] = now
        return profile

    def record_audit_log(
        self,
        command: str,
        actor_id: str,
        actor_username: str,
        decision: str,
        allowed: bool,
        reason: str,
        target: str | None = None,
    ) -> None:
        """Record an autonomous policy or command moderation decision."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_audit_log (command, actor_id, actor_username, decision, allowed, reason, target, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (command, str(actor_id), actor_username, decision, 1 if allowed else 0, reason, target, time.time()),
            )

    def get_recent_audit_logs(self, limit: int = 20) -> list[dict[str, object]]:
        """Fetch recent security and moderation audit log entries."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chat_mood(self, thread_id: str) -> dict[str, object]:
        """Fetch current mood state and aggression level for a chat thread."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_chat_mood WHERE thread_id = ?", (str(thread_id),)
            ).fetchone()
            if row:
                return dict(row)
            return {"thread_id": str(thread_id), "current_mood": "chill", "aggression_level": 0, "last_provocation_ts": 0.0}

    def set_chat_mood(self, thread_id: str, mood: str, aggression_level: int = 0) -> None:
        """Set current mood state and aggression level for a chat thread."""
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_chat_mood (thread_id, current_mood, aggression_level, last_provocation_ts, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    current_mood = excluded.current_mood,
                    aggression_level = excluded.aggression_level,
                    last_provocation_ts = CASE WHEN excluded.aggression_level > 0 THEN excluded.last_provocation_ts ELSE ai_chat_mood.last_provocation_ts END,
                    updated_at = excluded.updated_at
                """,
                (str(thread_id), mood, aggression_level, now, now),
            )


