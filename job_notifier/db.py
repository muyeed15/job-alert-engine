"""SQLite persistence (WAL mode): users, job history, and notification log.

The UNIQUE(job_id, user_id) constraint on ``notifications`` guarantees a user is never
alerted about the same job twice, even across restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import UserProfile
from .scraper import Job

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT NOT NULL,
    rules_json  TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT,
    location      TEXT,
    experience    TEXT,
    deadline      TEXT,
    deadline_iso  TEXT,
    salary        TEXT,
    job_type      TEXT,
    workplace     TEXT,
    category_ids  TEXT,
    posted_at     TEXT,
    fingerprint   TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    matched     TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT,
    UNIQUE (job_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_status ON notifications(user_id, status);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(self.path), timeout=30, isolation_level=None, check_same_thread=False
        )
        try:
            self.conn.row_factory = sqlite3.Row
            for pragma in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA foreign_keys=ON",
                "PRAGMA busy_timeout=10000",
                "PRAGMA cache_size=-2048",  # ~2 MB page cache to keep RSS low on Pi Zero 2 W
                "PRAGMA temp_store=FILE",
                "PRAGMA wal_autocheckpoint=500",
            ):
                self.conn.execute(pragma)
            with self._lock:
                self.conn.executescript(SCHEMA)
                existing_cols = {
                    r[1] for r in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
                }
                for col in ("job_type", "workplace"):
                    if col not in existing_cols:
                        self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
            with self.tx() as c:
                c.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        except BaseException:
            self.conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            finally:
                self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            else:
                self.conn.execute("COMMIT")

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ users

    def sync_users(self, users: Sequence[UserProfile]) -> dict[str, int]:
        """Upsert users from config; users missing from config are disabled (history kept)."""
        now = utcnow()
        ids: dict[str, int] = {}
        with self.tx() as c:
            for u in users:
                c.execute(
                    """
                    INSERT INTO users(email, name, rules_json, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        email = excluded.email,
                        name = excluded.name,
                        rules_json = excluded.rules_json,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        u.email,
                        u.name,
                        json.dumps(u.rules_dict(), sort_keys=True),
                        int(u.enabled),
                        now,
                        now,
                    ),
                )
                row = c.execute("SELECT id FROM users WHERE email = ?", (u.email,)).fetchone()
                ids[u.email] = row["id"]
            if users:
                emails = [u.email for u in users]
                placeholders = ",".join("?" * len(emails))
                c.execute(
                    f"UPDATE users SET enabled = 0, updated_at = ? WHERE enabled = 1 AND email NOT IN ({placeholders})",
                    (now, *emails),
                )
            else:
                c.execute("UPDATE users SET enabled = 0, updated_at = ? WHERE enabled = 1", (now,))
        return ids

    # ------------------------------------------------------------------ jobs

    def known_job_ids(self, job_ids: Iterable[str]) -> set:
        ids = list(dict.fromkeys(job_ids))
        found: set = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            rows = self.conn.execute(
                f"SELECT job_id FROM jobs WHERE job_id IN ({','.join('?' * len(chunk))})", chunk
            ).fetchall()
            found.update(r["job_id"] for r in rows)
        return found

    def all_known(self, job_ids: Iterable[str]) -> bool:
        ids = list(dict.fromkeys(job_ids))
        return bool(ids) and len(self.known_job_ids(ids)) == len(ids)

    def upsert_jobs(self, jobs: Iterable[Job]) -> list[str]:
        """Insert or refresh jobs. Returns the job_ids seen for the first time."""
        now = utcnow()
        new_ids: list[str] = []
        with self.tx() as c:
            for j in jobs:
                cats = ",".join(str(x) for x in sorted(j.category_ids))
                row = c.execute(
                    "SELECT category_ids FROM jobs WHERE job_id = ?", (j.job_id,)
                ).fetchone()
                if row is None:
                    new_ids.append(j.job_id)
                    c.execute(
                        """
                        INSERT INTO jobs(job_id, url, title, company, location, experience, deadline,
                                         deadline_iso, salary, job_type, workplace, category_ids,
                                         posted_at, fingerprint, discovered_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            j.job_id,
                            j.url,
                            j.title,
                            j.company,
                            j.location,
                            j.experience,
                            j.deadline,
                            j.deadline_iso,
                            j.salary,
                            j.job_type,
                            j.workplace,
                            cats,
                            j.posted_at,
                            j.fingerprint,
                            now,
                            now,
                        ),
                    )
                else:
                    merged = {
                        int(x)
                        for x in (row["category_ids"] or "").split(",")
                        if x.strip().lstrip("-").isdigit()
                    } | j.category_ids
                    c.execute(
                        """
                        UPDATE jobs SET url = ?, title = ?, company = ?, location = ?, experience = ?,
                               deadline = ?, deadline_iso = ?, salary = ?, job_type = ?, workplace = ?,
                               category_ids = ?, fingerprint = ?, last_seen_at = ?
                        WHERE job_id = ?
                        """,
                        (
                            j.url,
                            j.title,
                            j.company,
                            j.location,
                            j.experience,
                            j.deadline,
                            j.deadline_iso,
                            j.salary,
                            j.job_type,
                            j.workplace,
                            ",".join(str(x) for x in sorted(merged)),
                            j.fingerprint,
                            now,
                            j.job_id,
                        ),
                    )
        return new_ids

    # ------------------------------------------------------------------ notifications

    def enqueue_many(self, user_id: int, items: Sequence[tuple[Job, Sequence[str]]]) -> int:
        """Queue notifications for matched jobs in one transaction; returns how many are new.

        Jobs already in the user's log (any status) are ignored. A job whose fingerprint matches
        one the user was already alerted about (a re-post under a new ID) is logged as 'skipped'.
        """
        if not items:
            return 0
        deduped: dict[str, tuple[Job, Sequence[str]]] = {}
        for j, m in items:
            if j.job_id not in deduped:
                deduped[j.job_id] = (j, m)
        already = self.notified_job_ids(user_id, deduped.keys())
        todo = [pair for jid, pair in deduped.items() if jid not in already]
        if not todo:
            return 0
        queued = 0
        now = utcnow()
        with self.tx() as c:
            for job, matched in todo:
                repost = c.execute(
                    """
                    SELECT 1 FROM notifications n JOIN jobs j ON j.job_id = n.job_id
                    WHERE n.user_id = ? AND j.fingerprint = ? AND j.job_id <> ?
                      AND n.status IN ('pending', 'sent') LIMIT 1
                    """,
                    (user_id, job.fingerprint, job.job_id),
                ).fetchone()
                status = "skipped" if repost else "pending"
                cur = c.execute(
                    """
                    INSERT OR IGNORE INTO notifications(job_id, user_id, status, matched, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job.job_id, user_id, status, ",".join(matched), now),
                )
                if cur.rowcount == 1 and status == "pending":
                    queued += 1
        return queued

    def notified_job_ids(self, user_id: int, job_ids: Iterable[str]) -> set:
        ids = list(dict.fromkeys(job_ids))
        found: set = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            rows = self.conn.execute(
                f"SELECT job_id FROM notifications WHERE user_id = ? AND job_id IN ({','.join('?' * len(chunk))})",
                (user_id, *chunk),
            ).fetchall()
            found.update(r["job_id"] for r in rows)
        return found

    def pending_for_user(
        self, user_id: int, max_attempts: int, limit: int = 1000
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT n.id AS notification_id, n.matched, n.attempts, j.*
            FROM notifications n JOIN jobs j ON j.job_id = n.job_id
            WHERE n.user_id = ? AND n.status = 'pending' AND n.attempts < ?
            ORDER BY COALESCE(j.posted_at, j.discovered_at) DESC, j.job_id DESC
            LIMIT ?
            """,
            (user_id, max_attempts, limit),
        ).fetchall()

    def mark_sent(self, notification_ids: Sequence[int]) -> None:
        if not notification_ids:
            return
        now = utcnow()
        with self.tx() as c:
            c.executemany(
                "UPDATE notifications SET status = 'sent', sent_at = ?, last_error = NULL WHERE id = ?",
                [(now, i) for i in notification_ids],
            )

    def mark_failed_attempt(
        self, notification_ids: Sequence[int], error: str, max_attempts: int
    ) -> None:
        if not notification_ids:
            return
        with self.tx() as c:
            c.executemany(
                """
                UPDATE notifications
                SET attempts = attempts + 1, last_error = ?,
                    status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE status END
                WHERE id = ?
                """,
                [(error[:500], max_attempts, i) for i in notification_ids],
            )

    # ------------------------------------------------------------------ maintenance

    def delete_user(self, email: str) -> int:
        """Delete a user and their cascaded notifications from the database."""
        with self.tx() as c:
            cur = c.execute("DELETE FROM users WHERE email = ?", (email.strip().lower(),))
            return cur.rowcount

    def clear_pending_notifications(self) -> int:
        """Delete unsent pending notifications so updated user filters can re-evaluate cleanly."""
        with self.tx() as c:
            cur = c.execute("DELETE FROM notifications WHERE status = 'pending'")
            return cur.rowcount

    def clear_user_notifications(self, email: str) -> int:
        """Delete notification history for a single user."""
        with self.tx() as c:
            row = c.execute(
                "SELECT id FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
            if not row:
                return 0
            cur = c.execute("DELETE FROM notifications WHERE user_id = ?", (row["id"],))
            c.execute("DELETE FROM meta WHERE key = 'rules_hash'")
            return cur.rowcount

    def retry_failed_notifications(self, email: str | None = None) -> int:
        """Reset failed notifications back to pending with attempts = 0."""
        with self.tx() as c:
            if email:
                row = c.execute(
                    "SELECT id FROM users WHERE email = ?", (email.strip().lower(),)
                ).fetchone()
                if not row:
                    return 0
                cur = c.execute(
                    """
                    UPDATE notifications
                    SET status = 'pending', attempts = 0, last_error = NULL
                    WHERE user_id = ? AND status = 'failed'
                    """,
                    (row["id"],),
                )
            else:
                cur = c.execute(
                    """
                    UPDATE notifications
                    SET status = 'pending', attempts = 0, last_error = NULL
                    WHERE status = 'failed'
                    """
                )
            return cur.rowcount

    def clear_database(self, notifications_only: bool = False) -> dict[str, int]:
        """Clear notifications (and optionally all jobs and poll state) and compact the DB file."""
        with self.tx() as c:
            notif_deleted = c.execute("DELETE FROM notifications").rowcount
            jobs_deleted = 0
            if not notifications_only:
                jobs_deleted = c.execute("DELETE FROM jobs").rowcount
                c.execute("DELETE FROM meta WHERE key IN ('rules_hash', 'last_poll_at')")
            else:
                c.execute("DELETE FROM meta WHERE key = 'rules_hash'")
        with self._lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.execute("VACUUM")
            except sqlite3.Error as exc:
                log.warning("Database compact warning: %s", exc)
        return {"notifications_deleted": notif_deleted, "jobs_deleted": jobs_deleted}

    def purge_old_jobs(self, retention_days: int) -> int:
        """Delete jobs not seen for ``retention_days`` (their notifications cascade)."""
        cutoff = (
            (datetime.now(UTC) - timedelta(days=retention_days)).replace(microsecond=0).isoformat()
        )
        with self.tx() as c:
            cur = c.execute("DELETE FROM jobs WHERE last_seen_at < ?", (cutoff,))
            deleted = cur.rowcount
        with self._lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.execute("PRAGMA optimize")
            except sqlite3.Error as exc:
                log.warning("Database optimize warning: %s", exc)
        return deleted

    def stats(self) -> dict[str, int]:
        q = self.conn.execute
        out = {
            "users": q("SELECT COUNT(*) FROM users WHERE enabled = 1").fetchone()[0],
            "jobs": q("SELECT COUNT(*) FROM jobs").fetchone()[0],
        }
        for row in q("SELECT status, COUNT(*) AS n FROM notifications GROUP BY status"):
            out[f"notifications_{row['status']}"] = row["n"]
        return out
