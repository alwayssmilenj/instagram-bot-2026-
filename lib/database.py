"""SQLite state for deduplication, users, and Instagram group moderation."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config
import settings
from lib.memory_engine import (
    BM25Scorer,
    EmbeddingEngine,
    GroupLoreManager,
    HybridRanker,
    InsideJokeClusterer,
    MemoryDecay,
    SentimentTrajectoryAnalyzer,
    SocialRelationshipEngine,
    sqlite_cosine_blob,
)
from settings import DATABASE_PATH

_LEARN_PATTERNS = (
    ("nickname", "nickname", re.compile(r"\b(?:call me|my name is)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("preferred_address", "preferred_address", re.compile(r"\b(?:call me|address me as|call me ur|im ur|i am your)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("likes", "like", re.compile(r"\bi\s+(?:really\s+)?(?:like|love|enjoy)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("dislikes", "dislike", re.compile(r"\bi\s+(?:really\s+)?(?:hate|dislike|cant\s+stand|can\'t\s+stand)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("plays", "game", re.compile(r"\bi\s+(?:play|main)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "game", re.compile(r"\bmy\s+fav(?:orite)?\s+game\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "color", re.compile(r"\bmy\s+fav(?:orite)?\s+color\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "food", re.compile(r"\bmy\s+fav(?:orite)?\s+food\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "anime", re.compile(r"\bmy\s+fav(?:orite)?\s+anime\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "song", re.compile(r"\bmy\s+fav(?:orite)?\s+(?:song|track)\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("favorites", "artist", re.compile(r"\bmy\s+fav(?:orite)?\s+(?:artist|singer|band)\s+is\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("facts", "location", re.compile(r"\b(?:i live in|im from|i am from|i reside in)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("facts", "birthday", re.compile(r"\b(?:my birthday is|my bday is|born on)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("facts", "job", re.compile(r"\b(?:i work as|im a|i am a|my job is)\s+([a-zA-Z0-9_\- ]{2,40})")),
    ("taught", "taught", re.compile(r"\b(?:remember that|teach ineffa|learn that|keep in mind that)\s+([a-zA-Z0-9_\- :,.]{3,80})")),
    ("inside_joke", "joke", re.compile(r"\b(?:inside joke|our inside joke|our joke|remember the joke|new inside joke)\s*(?::|is|-|=|about)?\s*([a-zA-Z0-9_\- '\",.!?]{3,80})")),
)
_SPLIT_STOPWORDS_RE = re.compile(r"\b(?:and|but|because|when|while|though|although|so)\b")
_TOKEN_WORDS_RE = re.compile(r"\b\w+\b")
_CLEAN_USERNAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _cleanup_pool(connections: set[sqlite3.Connection], lock: threading.Lock) -> None:
    with lock:
        conns = list(connections)
        connections.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            pass


class SQLiteConnectionPool:
    """High-concurrency thread-local connection pool for SQLite with WAL optimizations."""

    def __init__(self, db_path: Path, max_age_seconds: float = 300.0) -> None:
        self.db_path = Path(db_path)
        self.max_age_seconds = max_age_seconds
        self._local = threading.local()
        self._all_connections: set[sqlite3.Connection] = set()
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _cleanup_pool, self._all_connections, self._lock)

    def get_connection(self) -> sqlite3.Connection:
        now = time.time()
        conn_record = getattr(self._local, "conn_record", None)
        if conn_record is not None:
            conn, created_at = conn_record
            if now - created_at < self.max_age_seconds:
                try:
                    conn.execute("SELECT 1")
                    return conn
                except Exception:
                    self._discard_connection(conn)
            else:
                self._discard_connection(conn)

        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.create_function("COSINE_SIM", 2, sqlite_cosine_blob)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA cache_size = -64000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        self._local.conn_record = (conn, now)
        with self._lock:
            self._all_connections.add(conn)
        return conn

    def _discard_connection(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            self._all_connections.discard(conn)
        try:
            conn.close()
        except Exception:
            pass
        self._local.conn_record = None

    def close_all(self) -> None:
        _cleanup_pool(self._all_connections, self._lock)
        self._local.conn_record = None

    def __del__(self) -> None:
        try:
            self.close_all()
        except Exception:
            pass


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
        self.bm25_scorer = BM25Scorer()
        self.joke_clusterer = InsideJokeClusterer()
        self.sentiment_analyzer = SentimentTrajectoryAnalyzer()
        self.social_engine = SocialRelationshipEngine()
        self.lore_manager = GroupLoreManager()
        self.pool = SQLiteConnectionPool(self.path)
        self._finalizer = weakref.finalize(self, self.pool.close_all)
        self._initialize()
        self.export_all_ai_memory()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # High-performance thread-local pooled connection.
        connection = self.pool.get_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                self.pool._discard_connection(connection)
            raise

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        """Close all pooled connections and release resources."""
        self.pool.close_all()

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
                CREATE INDEX IF NOT EXISTS idx_ai_episodes_user ON ai_episodes(user_id);
                CREATE INDEX IF NOT EXISTS idx_ai_episodes_session ON ai_episodes(session_key);
                CREATE INDEX IF NOT EXISTS idx_ai_episodes_milestone ON ai_episodes(is_milestone);
                CREATE INDEX IF NOT EXISTS idx_processed_messages_at ON processed_messages(processed_at);
                CREATE INDEX IF NOT EXISTS idx_users_msg_count ON users(message_count DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_user_facts_type ON ai_user_facts(fact_type, updated_at DESC);

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
                    rules TEXT NOT NULL DEFAULT '',
                    antiraid INTEGER NOT NULL DEFAULT 0,
                    raid_threshold INTEGER NOT NULL DEFAULT 5,
                    lockdown INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS gc_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_id TEXT,
                    target_username TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_gc_audit_thread ON gc_audit_log(thread_id, id DESC);
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

                CREATE TABLE IF NOT EXISTS gc_user_xp (
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    xp INTEGER NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    messages_count INTEGER NOT NULL DEFAULT 0,
                    last_active REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY(thread_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_gc_user_xp_rank ON gc_user_xp(thread_id, xp DESC);

                CREATE TABLE IF NOT EXISTS followed_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT,
                    followed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                -- Cognitive Enhancement 1: Multi-User Relationship Graph & Social Dynamics
                CREATE TABLE IF NOT EXISTS ai_user_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    source_user_id TEXT NOT NULL,
                    source_username TEXT NOT NULL,
                    target_user_id TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'neutral',
                    interaction_count INTEGER NOT NULL DEFAULT 1,
                    affinity_score REAL NOT NULL DEFAULT 0.0,
                    last_interaction_text TEXT,
                    updated_at REAL NOT NULL,
                    UNIQUE(thread_id, source_user_id, target_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_user_rel_thread ON ai_user_relationships(thread_id, affinity_score DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_user_rel_pair ON ai_user_relationships(source_user_id, target_user_id);

                -- Cognitive Enhancement 3: Persistent Group Lore & Collective Canon
                CREATE TABLE IF NOT EXISTS ai_group_lore (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    lore_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'gag',
                    significance INTEGER NOT NULL DEFAULT 5,
                    mentions_count INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(thread_id, lore_key)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_group_lore_thread ON ai_group_lore(thread_id, significance DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_group_lore_category ON ai_group_lore(thread_id, category);

                -- Cognitive Enhancement 4: Continuous Sentiment Trajectory History
                CREATE TABLE IF NOT EXISTS ai_sentiment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    valence REAL NOT NULL,
                    arousal REAL NOT NULL,
                    detected_vibe TEXT NOT NULL,
                    stress_flag INTEGER NOT NULL DEFAULT 0,
                    snippet TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_sentiment_user ON ai_sentiment_history(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_sentiment_thread ON ai_sentiment_history(thread_id, created_at DESC);

                -- Cognitive Enhancement 5: Inside Joke Clusters & Semantic Evolution
                CREATE TABLE IF NOT EXISTS ai_inside_joke_clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cluster_key TEXT NOT NULL,
                    thread_id TEXT,
                    user_id TEXT,
                    primary_phrase TEXT NOT NULL,
                    variants TEXT NOT NULL DEFAULT '[]',
                    usage_count INTEGER NOT NULL DEFAULT 1,
                    fun_rating REAL NOT NULL DEFAULT 5.0,
                    last_used_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(cluster_key, user_id, thread_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_joke_clusters_user ON ai_inside_joke_clusters(user_id, last_used_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_joke_clusters_thread ON ai_inside_joke_clusters(thread_id, usage_count DESC);

                -- Missing High-Performance Database Indexes
                CREATE INDEX IF NOT EXISTS idx_ai_working_created ON ai_working_memory(created_at);
                CREATE INDEX IF NOT EXISTS idx_thread_user_msg_count ON thread_user_messages(thread_id, message_count DESC);
                CREATE INDEX IF NOT EXISTS idx_banned_users_uid ON banned_users(user_id);
                CREATE INDEX IF NOT EXISTS idx_ai_user_facts_user_updated ON ai_user_facts(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_episodes_session_created ON ai_episodes(session_key, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_episodes_user_created ON ai_episodes(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gc_audit_created ON gc_audit_log(created_at DESC);
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
                "ALTER TABLE thread_settings ADD COLUMN antiraid INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE thread_settings ADD COLUMN raid_threshold INTEGER NOT NULL DEFAULT 5",
                "ALTER TABLE thread_settings ADD COLUMN lockdown INTEGER NOT NULL DEFAULT 0",
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
        clean_user = _CLEAN_USERNAME_RE.sub("_", username.lstrip("@")).strip("_") or "user"
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
        if "twin" in lowered and not any(p in lowered for p in ("twin turbo", "twin bed")):
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), "preferred_address", "preferred_address", "twin"),
            )
        for fact_type, key, pattern in _LEARN_PATTERNS:
            match = pattern.search(lowered)
            if not match:
                continue
            raw_value = match.group(1).strip(" .,!?:;")
            cleaned = _SPLIT_STOPWORDS_RE.split(raw_value)[0].strip()
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
        tokens = set(_TOKEN_WORDS_RE.findall(lowered))
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
                elif fact_type in {"inside_joke", "inside_jokes"}:
                    facts.setdefault("inside_jokes", {})[key] = value
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
            try:
                self.memory_dir.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                try:
                    temporary.chmod(0o600)
                except OSError:
                    pass
                temporary.replace(destination)
            except OSError:
                pass

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
            elif ft in {"inside_joke", "inside_jokes"}:
                details.append(f"inside joke ({fk}): {fv}")
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
            if user_id and clean_user and clean_user.lower() != settings.BOT_NAME.lower():
                self._learn_from_message(connection, str(user_id), message)
        try:
            self.append_working_turn(str(thread_id), str(user_id), clean_user, "user", message)
        except Exception:
            pass

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
        if key not in {"ai_auto_reply_dm", "ai_auto_reply_vn_dm", "tts_global_enabled", "auto_follow_back"}:
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
            "antiraid": bool(row["antiraid"]) if "antiraid" in row.keys() else False,
            "raid_threshold": int(row["raid_threshold"]) if "raid_threshold" in row.keys() else 5,
            "lockdown": bool(row["lockdown"]) if "lockdown" in row.keys() else False,
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
        if flag not in {
            "antilink", "antibadword", "antispam", "bot_muted", "admin_only",
            "ai_auto_reply", "ai_auto_reply_vn", "gc_monitor", "tts_enabled",
            "botgf_enabled", "antiraid", "lockdown",
        }:
            raise ValueError(f"Unknown thread setting: {flag}")
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute(f"UPDATE thread_settings SET {flag} = ? WHERE thread_id = ?", (int(enabled), str(thread_id)))
            if flag == "gc_monitor" and enabled and admin_id:
                connection.execute("UPDATE thread_settings SET gc_monitor_admin_id = ? WHERE thread_id = ?", (str(admin_id), str(thread_id)))

    def set_antiraid(self, thread_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute("UPDATE thread_settings SET antiraid = ? WHERE thread_id = ?", (int(enabled), str(thread_id)))

    def set_raid_threshold(self, thread_id: str, threshold: int) -> None:
        clamped = min(50, max(2, int(threshold)))
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute("UPDATE thread_settings SET raid_threshold = ? WHERE thread_id = ?", (clamped, str(thread_id)))

    def set_lockdown(self, thread_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO thread_settings(thread_id) VALUES (?)", (str(thread_id),))
            connection.execute("UPDATE thread_settings SET lockdown = ? WHERE thread_id = ?", (int(enabled), str(thread_id)))

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
        target = str(user_id).lower().lstrip("@")
        with self._connect() as connection:
            if str(thread_id) == "global":
                connection.execute(
                    "DELETE FROM banned_users WHERE thread_id = 'global' AND (user_id = ? OR LOWER(user_id) = ?)",
                    (str(user_id), target),
                )
            else:
                connection.execute(
                    "DELETE FROM banned_users WHERE thread_id = ? AND (user_id = ? OR LOWER(user_id) = ?)",
                    (str(thread_id), str(user_id), target),
                )

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

    def forget_fact(self, user_id: str, key: str) -> bool:
        key_clean = re.sub(r"[^a-z0-9_-]+", "_", key.strip().lower())[:40]
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_user_facts WHERE user_id = ? AND (fact_key = ? OR fact_type = ?)",
                (str(user_id), key_clean, key_clean),
            )
            deleted = cursor.rowcount > 0
            user_row = connection.execute("SELECT username FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        if user_row and user_row["username"]:
            self._sync_ai_memory_file(str(user_id), str(user_row["username"]))
        return deleted

    def list_taught_facts(self, user_id: str | None = None) -> list[dict[str, str]]:
        with self._connect() as connection:
            if user_id:
                rows = connection.execute(
                    "SELECT fact_type, fact_key, fact_value, updated_at FROM ai_user_facts WHERE user_id = ? ORDER BY updated_at DESC LIMIT 20",
                    (str(user_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT fact_type, fact_key, fact_value, updated_at FROM ai_user_facts WHERE fact_type = 'taught' ORDER BY updated_at DESC LIMIT 25",
                ).fetchall()
        return [
            {
                "type": str(r["fact_type"]),
                "key": str(r["fact_key"]),
                "value": str(r["fact_value"]),
                "updated_at": str(r["updated_at"] or ""),
            }
            for r in rows
        ]

    def get_user_facts(self, user_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fact_key, fact_value FROM ai_user_facts WHERE user_id = ? ORDER BY updated_at DESC LIMIT 15",
                (str(user_id),),
            ).fetchall()
        return {str(row["fact_key"]): str(row["fact_value"]) for row in rows}

    def store_inside_joke(self, user_id: str, key: str, value: str) -> None:
        key_clean = re.sub(r"[^a-z0-9_-]+", "_", key.strip().lower())[:40] or "joke"
        val_clean = " ".join(value.strip().split())[:200]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, 'inside_joke', ?, ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), key_clean, val_clean),
            )
            user_row = connection.execute("SELECT username FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        if user_row and user_row["username"]:
            self._sync_ai_memory_file(str(user_id), str(user_row["username"]))

    def get_inside_jokes(self, user_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT fact_key, fact_value, updated_at FROM ai_user_facts
                   WHERE user_id = ? AND (fact_type = 'inside_joke' OR fact_key LIKE 'joke_%')
                   ORDER BY updated_at DESC LIMIT 15""",
                (str(user_id),),
            ).fetchall()
        return [
            {"key": str(r["fact_key"]), "value": str(r["fact_value"]), "updated_at": str(r["updated_at"] or "")}
            for r in rows
        ]

    def store_nickname(self, user_id: str, nickname: str) -> None:
        nick_clean = " ".join(nickname.strip().split())[:35]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_user_facts(user_id, fact_type, fact_key, fact_value) VALUES (?, 'facts', 'nickname', ?)
                   ON CONFLICT(user_id, fact_type, fact_key) DO UPDATE SET
                     fact_value=excluded.fact_value, updated_at=CURRENT_TIMESTAMP""",
                (str(user_id), nick_clean),
            )
            user_row = connection.execute("SELECT username FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
        if user_row and user_row["username"]:
            self._sync_ai_memory_file(str(user_id), str(user_row["username"]))

    def get_nickname(self, user_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT fact_value FROM ai_user_facts
                   WHERE user_id = ? AND (fact_key = 'nickname' OR fact_type = 'nickname')
                   ORDER BY updated_at DESC LIMIT 1""",
                (str(user_id),),
            ).fetchone()
        return str(row["fact_value"]) if row else None

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

    def search_episodic_memories_hybrid(
        self,
        query: str,
        user_id: str = "",
        thread_id: str = "",
        top_k: int = 4,
    ) -> list[dict[str, Any]]:
        """Perform hybrid BM25 lexical + dense vector search with Ebbinghaus decay & emotional salience weighting."""
        query_clean = " ".join(query.strip().split())[:300]
        if not query_clean:
            return []

        query_vec = self.embedding_engine.embed_text(query_clean)
        query_blob = self.embedding_engine.pack_vector(query_vec)

        with self._connect() as connection:
            if thread_id:
                raw_rows = connection.execute(
                    """
                    SELECT id, user_id, session_key, summary, mood, significance, valence, is_milestone, milestone_type,
                           created_at, last_recalled_at, recall_count,
                           COSINE_SIM(embedding, ?) AS sim
                    FROM ai_episodes
                    WHERE (user_id = ? OR session_key = ? OR session_key = ?) AND embedding IS NOT NULL
                    ORDER BY sim DESC LIMIT 25
                    """,
                    (query_blob, str(user_id), str(thread_id), f"group:{thread_id}"),
                ).fetchall()
            else:
                raw_rows = connection.execute(
                    """
                    SELECT id, user_id, session_key, summary, mood, significance, valence, is_milestone, milestone_type,
                           created_at, last_recalled_at, recall_count,
                           COSINE_SIM(embedding, ?) AS sim
                    FROM ai_episodes
                    WHERE (user_id = ? OR session_key LIKE ?) AND embedding IS NOT NULL
                    ORDER BY sim DESC LIMIT 25
                    """,
                    (query_blob, str(user_id), f"%{user_id}%"),
                ).fetchall()

        if not raw_rows:
            return []

        vec_list: list[dict[str, Any]] = []
        for r in raw_rows:
            d = dict(r)
            d["retention"] = MemoryDecay.calculate_retention(
                float(d["created_at"]), float(d["last_recalled_at"]), int(d["significance"]), int(d["recall_count"])
            )
            vec_list.append(d)

        # Apply BM25 Lexical scoring
        bm25_results = self.bm25_scorer.score_documents(query_clean, vec_list, text_field="summary")

        # Fuse BM25 sparse results and dense cosine similarity results via weighted RRF
        fused = HybridRanker.fuse_results(bm25_results, vec_list, k=60, alpha=0.45)
        for item in fused[:top_k]:
            if "id" in item:
                self.touch_episode_recall(int(item["id"]))

        return fused[:top_k]

    def recall_relevant_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = 4,
        thread_id: str = "",
    ) -> list[dict[str, object]]:
        """Perform hybrid similarity retrieval over Episodic memory with time decay, including shared group chat memory."""
        return self.search_episodic_memories_hybrid(query, user_id=user_id, thread_id=thread_id, top_k=top_k)

    # -------------------------------------------------------------------------
    # Cognitive Enhancement 1: Multi-User Relationship Graph & Social Dynamics
    # -------------------------------------------------------------------------
    def record_social_interaction(
        self,
        thread_id: str,
        source_user_id: str,
        source_username: str,
        target_user_id: str,
        target_username: str,
        interaction_type: str = "neutral",
        delta_affinity: float = 0.0,
        snippet: str = "",
    ) -> dict[str, Any]:
        """Record directed social interaction between two users in a thread and update relationship graph."""
        now = time.time()
        src_uid = str(source_user_id)
        tgt_uid = str(target_user_id)
        tid = str(thread_id or "dm")
        src_name = str(source_username or src_uid).lstrip("@")
        tgt_name = str(target_username or tgt_uid).lstrip("@")
        clean_snippet = " ".join(snippet.strip().split())[:200]

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT interaction_count, affinity_score, relation_type
                FROM ai_user_relationships
                WHERE thread_id = ? AND source_user_id = ? AND target_user_id = ?
                """,
                (tid, src_uid, tgt_uid),
            ).fetchone()

            if row:
                new_count = int(row["interaction_count"]) + 1
                new_affinity = max(-10.0, min(10.0, float(row["affinity_score"]) + float(delta_affinity)))
            else:
                new_count = 1
                new_affinity = max(-10.0, min(10.0, float(delta_affinity)))

            calculated_type = SocialRelationshipEngine.classify_relationship(new_affinity, new_count)
            eff_type = interaction_type if interaction_type != "neutral" else calculated_type

            connection.execute(
                """
                INSERT INTO ai_user_relationships(
                    thread_id, source_user_id, source_username, target_user_id, target_username,
                    relation_type, interaction_count, affinity_score, last_interaction_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, source_user_id, target_user_id) DO UPDATE SET
                    source_username = excluded.source_username,
                    target_username = excluded.target_username,
                    relation_type = excluded.relation_type,
                    interaction_count = excluded.interaction_count,
                    affinity_score = excluded.affinity_score,
                    last_interaction_text = excluded.last_interaction_text,
                    updated_at = excluded.updated_at
                """,
                (
                    tid, src_uid, src_name, tgt_uid, tgt_name,
                    eff_type, new_count, new_affinity, clean_snippet, now
                ),
            )
            return {
                "thread_id": tid,
                "source_user_id": src_uid,
                "target_user_id": tgt_uid,
                "relation_type": eff_type,
                "affinity_score": new_affinity,
                "interaction_count": new_count,
            }

    def get_user_relationships(self, thread_id: str, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve top directed relationships for a user in a thread."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_user_id, source_username, target_user_id, target_username,
                       relation_type, interaction_count, affinity_score, last_interaction_text, updated_at
                FROM ai_user_relationships
                WHERE thread_id = ? AND (source_user_id = ? OR target_user_id = ?)
                ORDER BY interaction_count DESC, ABS(affinity_score) DESC
                LIMIT ?
                """,
                (str(thread_id), str(user_id), str(user_id), max(1, min(20, int(limit)))),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_thread_social_dynamics(self, thread_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve top interpersonal relationships across an entire group chat thread."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_user_id, source_username, target_user_id, target_username,
                       relation_type, interaction_count, affinity_score, last_interaction_text, updated_at
                FROM ai_user_relationships
                WHERE thread_id = ?
                ORDER BY interaction_count DESC, ABS(affinity_score) DESC
                LIMIT ?
                """,
                (str(thread_id), max(1, min(50, int(limit)))),
            ).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Cognitive Enhancement 3: Persistent Group Lore & Collective Canon
    # -------------------------------------------------------------------------
    def store_group_lore(
        self,
        thread_id: str,
        lore_key: str,
        title: str,
        content: str,
        category: str = "gag",
        significance: int = 5,
        created_by: str = "",
    ) -> None:
        """Store or update group lore, mythos, running gags, or canonical group rules."""
        now = time.time()
        key_clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", lore_key.strip().lower())[:40] or "lore"
        cat_clean = GroupLoreManager.detect_lore_category(content) if category == "gag" else category
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_group_lore(
                    thread_id, lore_key, title, content, category, significance, mentions_count, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(thread_id, lore_key) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    category = excluded.category,
                    significance = MAX(ai_group_lore.significance, excluded.significance),
                    mentions_count = ai_group_lore.mentions_count + 1,
                    updated_at = excluded.updated_at
                """,
                (str(thread_id), key_clean, str(title)[:100], str(content)[:600], cat_clean, max(1, min(10, int(significance))), str(created_by), now, now),
            )

    def get_group_lore(self, thread_id: str, category: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve group lore records for a thread."""
        with self._connect() as connection:
            if category:
                rows = connection.execute(
                    """
                    SELECT lore_key, title, content, category, significance, mentions_count, created_by, updated_at
                    FROM ai_group_lore
                    WHERE thread_id = ? AND category = ?
                    ORDER BY significance DESC, mentions_count DESC LIMIT ?
                    """,
                    (str(thread_id), str(category), max(1, min(50, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT lore_key, title, content, category, significance, mentions_count, created_by, updated_at
                    FROM ai_group_lore
                    WHERE thread_id = ?
                    ORDER BY significance DESC, mentions_count DESC LIMIT ?
                    """,
                    (str(thread_id), max(1, min(50, int(limit)))),
                ).fetchall()
            return [dict(r) for r in rows]

    def recall_relevant_group_lore(self, thread_id: str, query: str = "", limit: int = 4) -> list[dict[str, Any]]:
        """Recall group lore matching active conversation keywords or significance."""
        all_lore = self.get_group_lore(thread_id, limit=25)
        if not all_lore:
            return []
        if not query:
            return all_lore[:limit]

        query_tokens = set(re.findall(r"\b\w+\b", query.lower()))
        scored = []
        for item in all_lore:
            text = f"{item.get('lore_key', '')} {item.get('title', '')} {item.get('content', '')}".lower()
            tokens = set(re.findall(r"\b\w+\b", text))
            overlap = len(query_tokens & tokens)
            score = overlap * 2.0 + float(item.get("significance", 5)) * 0.5
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def delete_group_lore(self, thread_id: str, lore_key: str) -> bool:
        """Delete a group lore entry."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_group_lore WHERE thread_id = ? AND lore_key = ?",
                (str(thread_id), str(lore_key)),
            )
            return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Cognitive Enhancement 4: Continuous Sentiment Trajectory History
    # -------------------------------------------------------------------------
    def record_sentiment(
        self,
        user_id: str,
        thread_id: str,
        valence: float,
        arousal: float,
        vibe: str = "chill",
        snippet: str = "",
        stress_flag: bool = False,
    ) -> None:
        """Record sentiment snapshot for a user interaction."""
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_sentiment_history(user_id, thread_id, valence, arousal, detected_vibe, stress_flag, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user_id),
                    str(thread_id or "dm"),
                    max(-1.0, min(1.0, float(valence))),
                    max(0.0, min(1.0, float(arousal))),
                    str(vibe),
                    1 if stress_flag else 0,
                    str(snippet)[:300],
                    now,
                ),
            )
            # Prune ancient sentiment history beyond 100 entries per user
            connection.execute(
                """
                DELETE FROM ai_sentiment_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM ai_sentiment_history WHERE user_id = ? ORDER BY id DESC LIMIT 100
                )
                """,
                (str(user_id), str(user_id)),
            )

    def get_sentiment_history(self, user_id: str, thread_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent sentiment records for a user."""
        with self._connect() as connection:
            if thread_id:
                rows = connection.execute(
                    """
                    SELECT valence, arousal, detected_vibe, stress_flag, snippet, created_at
                    FROM ai_sentiment_history
                    WHERE user_id = ? AND thread_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (str(user_id), str(thread_id), max(1, min(50, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT valence, arousal, detected_vibe, stress_flag, snippet, created_at
                    FROM ai_sentiment_history
                    WHERE user_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (str(user_id), max(1, min(50, int(limit)))),
                ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_user_sentiment_trajectory(self, user_id: str, thread_id: str | None = None) -> dict[str, Any]:
        """Compute rolling emotional trajectory and stress status for a user."""
        history = self.get_sentiment_history(user_id, thread_id=thread_id, limit=12)
        return SentimentTrajectoryAnalyzer.calculate_trajectory(history)

    # -------------------------------------------------------------------------
    # Cognitive Enhancement 5: Inside Joke Clusters & Semantic Evolution
    # -------------------------------------------------------------------------
    def record_inside_joke_cluster(
        self,
        cluster_key: str,
        primary_phrase: str,
        thread_id: str | None = None,
        user_id: str | None = None,
        variant: str | None = None,
        fun_rating: float = 5.0,
    ) -> dict[str, Any]:
        """Store or evolve an inside joke cluster with automatic variant clustering."""
        now = time.time()
        c_key = InsideJokeClusterer.slugify(cluster_key or primary_phrase)
        tid = str(thread_id) if thread_id is not None else None
        uid = str(user_id) if user_id is not None else None
        p_phrase = " ".join(primary_phrase.strip().split())[:150]

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, primary_phrase, variants, usage_count, fun_rating
                FROM ai_inside_joke_clusters
                WHERE cluster_key = ? AND (user_id IS ? OR user_id = ?) AND (thread_id IS ? OR thread_id = ?)
                """,
                (c_key, uid, uid, tid, tid),
            ).fetchone()

            if row:
                variants = json.loads(row["variants"]) if row["variants"] else []
                if variant and variant not in variants and variant != row["primary_phrase"]:
                    variants.append(str(variant)[:150])
                new_count = int(row["usage_count"]) + 1
                new_rating = max(1.0, min(10.0, float(row["fun_rating"]) + 0.2))
                connection.execute(
                    """
                    UPDATE ai_inside_joke_clusters
                    SET variants = ?, usage_count = ?, fun_rating = ?, last_used_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(variants[-10:], ensure_ascii=False), new_count, new_rating, now, int(row["id"])),
                )
                return {
                    "cluster_key": c_key,
                    "primary_phrase": str(row["primary_phrase"]),
                    "variants": variants,
                    "usage_count": new_count,
                    "fun_rating": new_rating,
                }
            else:
                variants = [variant] if variant and variant != p_phrase else []
                connection.execute(
                    """
                    INSERT INTO ai_inside_joke_clusters(
                        cluster_key, thread_id, user_id, primary_phrase, variants, usage_count, fun_rating, last_used_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (c_key, tid, uid, p_phrase, json.dumps(variants, ensure_ascii=False), float(fun_rating), now, now),
                )
                return {
                    "cluster_key": c_key,
                    "primary_phrase": p_phrase,
                    "variants": variants,
                    "usage_count": 1,
                    "fun_rating": float(fun_rating),
                }

    def get_inside_joke_clusters(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Retrieve inside joke clusters for a user and/or thread."""
        with self._connect() as connection:
            if user_id and thread_id:
                rows = connection.execute(
                    """
                    SELECT cluster_key, primary_phrase, variants, usage_count, fun_rating, last_used_at
                    FROM ai_inside_joke_clusters
                    WHERE user_id = ? OR thread_id = ? OR (user_id IS NULL AND thread_id IS NULL)
                    ORDER BY usage_count DESC, fun_rating DESC LIMIT ?
                    """,
                    (str(user_id), str(thread_id), max(1, min(50, int(limit)))),
                ).fetchall()
            elif user_id:
                rows = connection.execute(
                    """
                    SELECT cluster_key, primary_phrase, variants, usage_count, fun_rating, last_used_at
                    FROM ai_inside_joke_clusters
                    WHERE user_id = ? OR user_id IS NULL
                    ORDER BY usage_count DESC, fun_rating DESC LIMIT ?
                    """,
                    (str(user_id), max(1, min(50, int(limit)))),
                ).fetchall()
            elif thread_id:
                rows = connection.execute(
                    """
                    SELECT cluster_key, primary_phrase, variants, usage_count, fun_rating, last_used_at
                    FROM ai_inside_joke_clusters
                    WHERE thread_id = ? OR thread_id IS NULL
                    ORDER BY usage_count DESC, fun_rating DESC LIMIT ?
                    """,
                    (str(thread_id), max(1, min(50, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT cluster_key, primary_phrase, variants, usage_count, fun_rating, last_used_at
                    FROM ai_inside_joke_clusters
                    ORDER BY usage_count DESC, fun_rating DESC LIMIT ?
                    """,
                    (max(1, min(50, int(limit))),),
                ).fetchall()

            res: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["variants"] = json.loads(d["variants"]) if d.get("variants") else []
                res.append(d)
            return res

    def recall_matching_joke_clusters(
        self,
        user_id: str = "",
        thread_id: str = "",
        query: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find inside joke clusters matching conversational keywords or semantic similarity."""
        clusters = self.get_inside_joke_clusters(user_id=user_id or None, thread_id=thread_id or None, limit=20)
        if not clusters:
            return []
        if not query:
            return clusters[:limit]

        scored = []
        for cl in clusters:
            sim = InsideJokeClusterer.similarity(query, cl["primary_phrase"])
            for var in cl.get("variants", []):
                sim = max(sim, InsideJokeClusterer.similarity(query, var))
            score = sim * 5.0 + float(cl.get("usage_count", 1)) * 0.2
            if score > 0.1:
                scored.append((score, cl))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [cl for _, cl in scored[:limit]]

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

    def log_audit_event(
        self,
        thread_id: str,
        actor_id: str,
        actor_username: str,
        action: str,
        target_id: str | None = None,
        target_username: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Log a moderation, anti-raid, or security activity event to gc_audit_log."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO gc_audit_log (thread_id, actor_id, actor_username, action, target_id, target_username, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(thread_id),
                    str(actor_id),
                    str(actor_username or actor_id),
                    str(action),
                    str(target_id) if target_id is not None else None,
                    str(target_username) if target_username is not None else None,
                    str(reason) if reason is not None else None,
                ),
            )
            return int(cursor.lastrowid or 0)

    def get_recent_audit_logs(self, thread_id: str | None = None, limit: int = 10) -> list[dict[str, object]]:
        """Fetch recent security and moderation audit log entries for a thread (or globally)."""
        limit_val = min(100, max(1, int(limit)))
        with self._connect() as connection:
            if thread_id is not None:
                rows = connection.execute(
                    "SELECT * FROM gc_audit_log WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
                    (str(thread_id), limit_val),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM gc_audit_log ORDER BY id DESC LIMIT ?",
                    (limit_val,),
                ).fetchall()
                if not rows:
                    rows = connection.execute(
                        "SELECT * FROM ai_audit_log ORDER BY id DESC LIMIT ?",
                        (limit_val,),
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

    # -------------------------------------------------------------------------
    # Group Chat XP, Leveling & Gamification System
    # -------------------------------------------------------------------------
    def add_user_xp(self, thread_id: str, user_id: str, username: str, amount: int = 10) -> tuple[int, int, bool]:
        """Awards XP to a user in a group chat with level-up detection."""
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT xp, level, messages_count FROM gc_user_xp WHERE thread_id = ? AND user_id = ?",
                (str(thread_id), str(user_id)),
            ).fetchone()
            
            if row:
                curr_xp = int(row["xp"]) + amount
                curr_lvl = int(row["level"])
                msgs = int(row["messages_count"]) + 1
            else:
                curr_xp = amount
                curr_lvl = 1
                msgs = 1
                
            new_lvl = max(1, int(1 + (curr_xp / 100) ** 0.5))
            leveled_up = new_lvl > curr_lvl

            connection.execute(
                """
                INSERT INTO gc_user_xp(thread_id, user_id, username, xp, level, messages_count, last_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    xp = excluded.xp,
                    level = excluded.level,
                    messages_count = excluded.messages_count,
                    last_active = excluded.last_active
                """,
                (str(thread_id), str(user_id), str(username or ""), curr_xp, new_lvl, msgs, now),
            )
            return curr_xp, new_lvl, leveled_up

    def get_user_rank(self, thread_id: str, user_id: str) -> dict[str, object] | None:
        """Fetch user XP stats, calculated level title, and current standing in the group."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT xp, level, messages_count, username FROM gc_user_xp WHERE thread_id = ? AND user_id = ?",
                (str(thread_id), str(user_id)),
            ).fetchone()
            if not row:
                return None
            rank_row = connection.execute(
                "SELECT COUNT(*) + 1 AS rank FROM gc_user_xp WHERE thread_id = ? AND xp > ?",
                (str(thread_id), int(row["xp"])),
            ).fetchone()
            rank = int(rank_row["rank"]) if rank_row else 1
            lvl = int(row["level"])
            
            # Title based on level
            if lvl >= 50:
                title = "⚡ Mythic Sovereign"
            elif lvl >= 35:
                title = "👑 High Luminary"
            elif lvl >= 20:
                title = "🌟 Grand Vanguard"
            elif lvl >= 10:
                title = "🛡️ Elite Guardian"
            elif lvl >= 5:
                title = "⚔️ Vanguard Luminary"
            else:
                title = "🌱 Novice Wanderer"
                
            return {
                "xp": int(row["xp"]),
                "level": lvl,
                "messages_count": int(row["messages_count"]),
                "username": str(row["username"]),
                "rank": rank,
                "title": title,
            }

    def sync_entire_thread_leaderboard(self, thread_id: str) -> None:
        """Scan historical message tables and ensure all members in thread_id have accurate synced XP in gc_user_xp."""
        with self._connect() as connection:
            tum_rows = connection.execute(
                "SELECT user_id, username, message_count FROM thread_user_messages WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchall()
            atc_rows = connection.execute(
                """
                SELECT user_id, username, COUNT(*) AS count
                FROM ai_thread_context
                WHERE thread_id = ?
                GROUP BY user_id
                """,
                (str(thread_id),),
            ).fetchall()

            user_stats: dict[str, dict[str, object]] = {}
            for row in tum_rows:
                uid = str(row["user_id"])
                uname = str(row["username"] or "").lstrip("@")
                cnt = int(row["message_count"])
                user_stats[uid] = {"username": uname, "messages_count": cnt}

            for row in atc_rows:
                uid = str(row["user_id"])
                uname = str(row["username"] or "").lstrip("@")
                cnt = int(row["count"])
                if uid not in user_stats:
                    user_stats[uid] = {"username": uname, "messages_count": cnt}
                else:
                    user_stats[uid]["messages_count"] = max(int(user_stats[uid]["messages_count"]), cnt)
                    if not user_stats[uid]["username"] and uname:
                        user_stats[uid]["username"] = uname

            now = time.time()
            for uid, data in user_stats.items():
                msgs = max(1, int(data["messages_count"]))
                curr = connection.execute(
                    "SELECT xp FROM gc_user_xp WHERE thread_id = ? AND user_id = ?",
                    (str(thread_id), uid),
                ).fetchone()
                existing_xp = int(curr["xp"]) if curr else 0
                calc_xp = max(existing_xp, msgs * 10)
                calc_lvl = max(1, int(1 + (calc_xp / 100) ** 0.5))
                uname = str(data["username"] or uid)

                connection.execute(
                    """
                    INSERT INTO gc_user_xp(thread_id, user_id, username, xp, level, messages_count, last_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id, user_id) DO UPDATE SET
                        username = CASE WHEN excluded.username != '' THEN excluded.username ELSE gc_user_xp.username END,
                        xp = excluded.xp,
                        level = excluded.level,
                        messages_count = excluded.messages_count,
                        last_active = excluded.last_active
                    """,
                    (str(thread_id), uid, uname, calc_xp, calc_lvl, msgs, now),
                )

    def get_gc_xp_leaderboard(self, thread_id: str, limit: int = 10) -> list[dict[str, object]]:
        """Fetch the top active members ranked by XP in a group chat (or global if thread has no entries)."""
        self.sync_entire_thread_leaderboard(thread_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, username, xp, level, messages_count
                FROM gc_user_xp
                WHERE thread_id = ?
                ORDER BY xp DESC LIMIT ?
                """,
                (str(thread_id), max(1, min(50, limit))),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """
                    SELECT user_id, username, MAX(xp) as xp, MAX(level) as level, SUM(messages_count) as messages_count
                    FROM gc_user_xp
                    GROUP BY user_id
                    ORDER BY xp DESC LIMIT ?
                    """,
                    (max(1, min(50, limit)),),
                ).fetchall()
            results: list[dict[str, object]] = []
            for r in rows:
                d = dict(r)
                lvl = int(d.get("level", 1))
                if lvl >= 50:
                    d["title"] = "⚡ Mythic Sovereign"
                elif lvl >= 35:
                    d["title"] = "👑 High Luminary"
                elif lvl >= 20:
                    d["title"] = "🌟 Grand Vanguard"
                elif lvl >= 10:
                    d["title"] = "🛡️ Elite Guardian"
                elif lvl >= 5:
                    d["title"] = "⚔️ Vanguard Luminary"
                else:
                    d["title"] = "🌱 Novice Wanderer"
                results.append(d)
            return results

    def is_user_followed(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM followed_users WHERE user_id = ?", (str(user_id),)).fetchone()
        return row is not None

    def mark_user_followed(self, user_id: str, username: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO followed_users(user_id, username) VALUES (?, ?)",
                (str(user_id), str(username or "")),
            )

    def get_followed_users_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM followed_users").fetchone()
            return int(row["count"]) if row else 0

    def vacuum(self) -> None:
        with self._connect() as connection:
            connection.execute("VACUUM")

    def sync_full_chat_history_xp(self, thread_id: str, user_id: str, username: str = "") -> dict[str, object]:
        """Sync and recompute user's XP and Level from full chat message history in the database."""
        with self._connect() as connection:
            # 1. Check thread_user_messages
            tum_row = connection.execute(
                "SELECT message_count FROM thread_user_messages WHERE thread_id = ? AND user_id = ?",
                (str(thread_id), str(user_id)),
            ).fetchone()
            tum_msgs = int(tum_row["message_count"]) if tum_row else 0

            # 2. Check ai_thread_context
            cnt_row = connection.execute(
                """
                SELECT COUNT(*) AS total_msgs
                FROM ai_thread_context
                WHERE thread_id = ? AND (user_id = ? OR (username != '' AND LOWER(username) = LOWER(?)))
                """,
                (str(thread_id), str(user_id), str(username or "").lstrip("@")),
            ).fetchone()
            db_msgs = int(cnt_row["total_msgs"]) if cnt_row else 0

            # 3. Check global users table
            user_row = connection.execute(
                "SELECT message_count FROM users WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            user_msgs = int(user_row["message_count"]) if user_row else 0

            # 4. Check existing gc_user_xp
            curr_row = connection.execute(
                "SELECT xp, level, messages_count FROM gc_user_xp WHERE thread_id = ? AND user_id = ?",
                (str(thread_id), str(user_id)),
            ).fetchone()

            existing_msgs = int(curr_row["messages_count"]) if curr_row else 0
            existing_xp = int(curr_row["xp"]) if curr_row else 0

            effective_msgs = max(existing_msgs, tum_msgs, db_msgs, user_msgs, 1)
            calculated_xp = max(existing_xp, effective_msgs * 10)
            calculated_lvl = max(1, int(1 + (calculated_xp / 100) ** 0.5))
            now = time.time()
            connection.execute(
                """
                INSERT INTO gc_user_xp(thread_id, user_id, username, xp, level, messages_count, last_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    xp = excluded.xp,
                    level = excluded.level,
                    messages_count = excluded.messages_count,
                    last_active = excluded.last_active
                """,
                (str(thread_id), str(user_id), str(username or ""), calculated_xp, calculated_lvl, effective_msgs, now),
            )
            return {
                "xp": calculated_xp,
                "level": calculated_lvl,
                "messages_count": effective_msgs,
                "username": username,
            }

    def get_full_user_profile_stats(self, thread_id: str, user_id: str = "", username: str = "") -> dict[str, object]:
        """Fetch 100% real user statistics, real rank, real aura, real badges, and real level for profile cards."""
        clean_user = username.lstrip("@").strip()
        clean_uid = str(user_id).strip()

        with self._connect() as connection:
            row = None
            if clean_uid:
                row = connection.execute(
                    "SELECT user_id, username, xp, level, messages_count FROM gc_user_xp WHERE thread_id = ? AND user_id = ?",
                    (str(thread_id), clean_uid),
                ).fetchone()
            if not row and clean_user:
                row = connection.execute(
                    "SELECT user_id, username, xp, level, messages_count FROM gc_user_xp WHERE thread_id = ? AND LOWER(username) = LOWER(?)",
                    (str(thread_id), clean_user),
                ).fetchone()
            if not row and clean_user:
                row = connection.execute(
                    "SELECT user_id, username, xp, level, messages_count FROM gc_user_xp WHERE LOWER(username) = LOWER(?) ORDER BY xp DESC LIMIT 1",
                    (clean_user,),
                ).fetchone()

            if row:
                eff_uid = str(row["user_id"])
                eff_uname = str(row["username"] or clean_user or eff_uid)
                xp = int(row["xp"])
                level = int(row["level"])
                messages_count = int(row["messages_count"])
            else:
                eff_uid = clean_uid or clean_user or "wanderer"
                eff_uname = clean_user or clean_uid or "Wanderer"
                xp = 100
                level = 1
                messages_count = 10

            rank_row = connection.execute(
                "SELECT COUNT(*) + 1 AS rank FROM gc_user_xp WHERE thread_id = ? AND xp > ?",
                (str(thread_id), xp),
            ).fetchone()
            rank = int(rank_row["rank"]) if rank_row else 1

            if level >= 50:
                title = "⚡ Mythic Sovereign"
            elif level >= 35:
                title = "👑 High Luminary"
            elif level >= 20:
                title = "🌟 Grand Vanguard"
            elif level >= 10:
                title = "🛡️ Elite Guardian"
            elif level >= 5:
                title = "⚔️ Vanguard Luminary"
            else:
                title = "🌱 Novice Wanderer"

            aura_points = min(99999, max(50, int(xp * 1.5 + messages_count * 12)))
            if aura_points >= 50000:
                aura_tier = "⚡ Celestial Sovereign"
            elif aura_points >= 20000:
                aura_tier = "🌟 Mythic Luminary"
            elif aura_points >= 10000:
                aura_tier = "👑 Grand Vanguard"
            elif aura_points >= 4000:
                aura_tier = "🛡️ Elite Guardian"
            elif aura_points >= 1500:
                aura_tier = "✨ Awakened Sentinel"
            elif aura_points >= 500:
                aura_tier = "💫 Radiant Stargazer"
            else:
                aura_tier = "🌱 Novice Wanderer"

            badges: list[str] = []
            if config.is_owner(eff_uname, eff_uid):
                badges.append("👑 Bot Owner")
            if rank == 1:
                badges.append("🏆 #1 Rank")
            elif rank <= 3:
                badges.append("⚡ Top 3 Legend")
            if messages_count >= 500:
                badges.append("🔥 Chat God")
            elif messages_count >= 100:
                badges.append("⚔️ Chat Veteran")
            elif messages_count >= 25:
                badges.append("💬 Active Chatter")
            if level >= 20:
                badges.append("🌟 High Tier")
            elif level >= 5:
                badges.append("💠 Vanguard")
            if not badges:
                badges = ["🌱 Newcomer", "🛡️ Member"]

            return {
                "user_id": eff_uid,
                "username": eff_uname,
                "xp": xp,
                "level": level,
                "rank": rank,
                "messages_count": messages_count,
                "title": title,
                "aura_points": aura_points,
                "aura_tier": aura_tier,
                "badges": badges[:3],
            }



