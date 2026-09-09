"""SQLite data layer for the IT Insights Lab bot.

A thin, explicit data-access layer over sqlite3. The database file is
created and migrated automatically on startup; every call opens a fresh
connection guarded by a lock so the module is safe under PTB's async
event loop running handlers on one thread.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,          -- Telegram user id
    username       TEXT,
    full_name      TEXT NOT NULL,
    city           TEXT NOT NULL,
    occupation     TEXT NOT NULL,
    photo_file_id  TEXT NOT NULL,
    bio            TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'banned')),
    registered_at  TEXT NOT NULL,
    banned_at      TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL,
    attachment_file_id TEXT,
    attachment_kind  TEXT,
    deadline         TEXT,
    created_at       TEXT NOT NULL,
    created_by       INTEGER
);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    task_id      INTEGER NOT NULL REFERENCES tasks(id),
    kind         TEXT NOT NULL CHECK (kind IN ('file', 'link', 'text')),
    content      TEXT,
    file_id      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submissions_task ON submissions(task_id);
CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
"""

_lock = threading.Lock()
_db_path: Path | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Database not initialised; call init_db() first.")
    conn = sqlite3.connect(_db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _execute(query: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(query, params)
            conn.commit()
            return cur
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _query_all(query: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        conn = _connect()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()


def _query_one(query: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    rows = _query_all(query, params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column migrations for databases created by older versions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(submissions)")}  # r[1] = column name
    additions = {
        "review_chat_id": "INTEGER",
        "review_message_id": "INTEGER",
        "reviewed_by": "INTEGER",
        "reviewed_at": "TEXT",
    }
    for name, decl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE submissions ADD COLUMN {name} {decl}")


def init_db(path: str | Path) -> None:
    """Create/open the database and apply the schema. Safe to call repeatedly."""
    global _db_path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(target)
        try:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
    _db_path = target
    logger.info("Database ready at %s", target)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def register_user(
    *,
    user_id: int,
    username: str | None,
    full_name: str,
    city: str,
    occupation: str,
    photo_file_id: str,
    bio: str,
) -> bool:
    """Insert or update a user profile. Returns True when a new row was created."""
    cur = _execute(
        """
        INSERT INTO users (id, username, full_name, city, occupation, photo_file_id, bio,
                           status, registered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
        ON CONFLICT(id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            city = excluded.city,
            occupation = excluded.occupation,
            photo_file_id = excluded.photo_file_id,
            bio = excluded.bio,
            status = 'active',
            banned_at = NULL
        """,
        (user_id, username, full_name, city, occupation, photo_file_id, bio, _now()),
    )
    return cur.rowcount > 0


def get_user(user_id: int) -> sqlite3.Row | None:
    return _query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def set_user_status(user_id: int, status: str) -> bool:
    """Set a user's status to 'active' or 'banned'. Returns True when a row changed."""
    if status not in {"active", "banned"}:
        raise ValueError(f"Invalid status: {status!r}")
    banned_at = _now() if status == "banned" else None
    cur = _execute(
        "UPDATE users SET status = ?, banned_at = ? WHERE id = ?",
        (status, banned_at, user_id),
    )
    return cur.rowcount > 0


def delete_user(user_id: int) -> bool:
    """Remove a user and all of their submissions (FK-safe cascade)."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM submissions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def count_users(status: str | None = None) -> int:
    if status:
        row = _query_one("SELECT COUNT(*) AS n FROM users WHERE status = ?", (status,))
    else:
        row = _query_one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def list_users(offset: int = 0, limit: int = 8, status: str | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM users"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY registered_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return _query_all(query, params)


def list_all_user_ids(active_only: bool = True) -> list[int]:
    if active_only:
        rows = _query_all("SELECT id FROM users WHERE status = 'active'")
    else:
        rows = _query_all("SELECT id FROM users")
    return [int(r["id"]) for r in rows]


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
def create_task(
    *,
    title: str,
    description: str,
    attachment_file_id: str | None = None,
    attachment_kind: str | None = None,
    deadline: str | None = None,
    created_by: int | None = None,
) -> int:
    cur = _execute(
        """
        INSERT INTO tasks (title, description, attachment_file_id, attachment_kind,
                           deadline, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (title, description, attachment_file_id, attachment_kind, deadline, _now(), created_by),
    )
    return int(cur.lastrowid)


def get_task(task_id: int) -> sqlite3.Row | None:
    return _query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))


def list_tasks() -> list[sqlite3.Row]:
    return _query_all("SELECT * FROM tasks ORDER BY id DESC")


def count_tasks() -> int:
    row = _query_one("SELECT COUNT(*) AS n FROM tasks")
    return int(row["n"]) if row else 0


# --------------------------------------------------------------------------- #
# Submissions
# --------------------------------------------------------------------------- #
def add_submission(
    *,
    user_id: int,
    task_id: int,
    kind: str,
    content: str | None = None,
    file_id: str | None = None,
) -> int:
    cur = _execute(
        """
        INSERT INTO submissions (user_id, task_id, kind, content, file_id, status, submitted_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (user_id, task_id, kind, content, file_id, _now()),
    )
    return int(cur.lastrowid)


def get_submission(submission_id: int) -> sqlite3.Row | None:
    return _query_one("SELECT * FROM submissions WHERE id = ?", (submission_id,))


def list_submissions(task_id: int | None = None, user_id: int | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT s.*, u.full_name, u.username, t.title AS task_title
        FROM submissions s
        JOIN users u ON u.id = s.user_id
        JOIN tasks t ON t.id = s.task_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if task_id is not None:
        query += " AND s.task_id = ?"
        params.append(task_id)
    if user_id is not None:
        query += " AND s.user_id = ?"
        params.append(user_id)
    query += " ORDER BY s.submitted_at DESC"
    return _query_all(query, params)


def delete_task(task_id: int) -> bool:
    """Remove a task and its submissions (FK-safe cascade)."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM submissions WHERE task_id = ?", (task_id,))
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def count_submissions_per_task() -> list[sqlite3.Row]:
    return _query_all(
        """
        SELECT t.id, t.title, COUNT(s.id) AS submissions
        FROM tasks t
        LEFT JOIN submissions s ON s.task_id = t.id
        GROUP BY t.id, t.title
        ORDER BY t.id DESC
        """
    )


def count_submissions(user_id: int | None = None, task_id: int | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM submissions WHERE 1 = 1"
    params: list[Any] = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    if task_id is not None:
        query += " AND task_id = ?"
        params.append(task_id)
    row = _query_one(query, params)
    return int(row["n"]) if row else 0


def count_pending_submissions() -> int:
    row = _query_one("SELECT COUNT(*) AS n FROM submissions WHERE status = 'pending'")
    return int(row["n"]) if row else 0


def set_submission_status(submission_id: int, status: str) -> bool:
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError(f"Invalid status: {status!r}")
    cur = _execute("UPDATE submissions SET status = ? WHERE id = ?", (status, submission_id))
    return cur.rowcount > 0


def set_submission_review_post(submission_id: int, chat_id: int, message_id: int) -> None:
    """Remember the reviewers-group post for this submission (enables reply-based review)."""
    _execute(
        "UPDATE submissions SET review_chat_id = ?, review_message_id = ? WHERE id = ?",
        (chat_id, message_id, submission_id),
    )


def get_submission_by_review_message(chat_id: int, message_id: int) -> sqlite3.Row | None:
    return _query_one(
        "SELECT * FROM submissions WHERE review_chat_id = ? AND review_message_id = ?",
        (chat_id, message_id),
    )


def review_submission(submission_id: int, status: str, reviewer_id: int) -> bool:
    """Mark a submission approved/rejected and stamp who and when."""
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Invalid review status: {status!r}")
    cur = _execute(
        "UPDATE submissions SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (status, reviewer_id, _now(), submission_id),
    )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Export helpers
# --------------------------------------------------------------------------- #
def iter_users_for_export() -> Iterator[sqlite3.Row]:
    yield from _query_all("SELECT * FROM users ORDER BY registered_at")


def iter_submissions_for_export() -> Iterator[sqlite3.Row]:
    yield from _query_all(
        """
        SELECT s.id, s.user_id, u.full_name, u.username, s.task_id, t.title AS task_title,
               s.kind, s.content, s.file_id, s.status, s.submitted_at
        FROM submissions s
        JOIN users u ON u.id = s.user_id
        JOIN tasks t ON t.id = s.task_id
        ORDER BY s.submitted_at
        """
    )
