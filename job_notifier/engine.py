"""Orchestration: fetch -> store -> match -> queue -> dispatch."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace

from .config import AppConfig, ConfigError, UserProfile, load_config, load_env
from .db import Database, utcnow
from .mailer import Mailer, MailError
from .matcher import Matcher
from .scraper import Job, JobScraper, ScrapeError
from .templates import AlertItem, EmailContent, build_digest_email, build_instant_email

log = logging.getLogger(__name__)


class _AbortDispatch(Exception):
    pass


@dataclass
class PollReport:
    fetched: int = 0
    new_jobs: int = 0
    queued: dict[str, int] = field(default_factory=dict)
    full_scan: bool = False
    error: str | None = None

    def summary(self) -> str:
        q = sum(self.queued.values())
        err = f" error={self.error}" if self.error else ""
        return (
            f"fetched={self.fetched} new_jobs={self.new_jobs} queued_notifications={q}"
            f"{' (full scan)' if self.full_scan else ''}{err}"
        )


def categories_to_fetch(users: Sequence[UserProfile]) -> list[int | None]:
    """Union of all users' categories; ``[None]`` (= all categories) if any user wants everything."""
    if not users:
        return []
    if any(not u.categories for u in users):
        return [None]
    return sorted({c for u in users for c in u.categories})


class AlertEngine:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        scraper: JobScraper,
        mailer: Mailer | None,
    ):
        self.db = db
        self.scraper = scraper
        self.mailer = mailer
        self.config = config
        self.matcher = Matcher(config.active_users)
        self.user_ids: dict[str, int] = {}
        self.apply_config(config)

    # ------------------------------------------------------------------ config

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self.matcher = Matcher(config.active_users)
        self.user_ids = self.db.sync_users(config.users)
        self.scraper.settings = config.settings
        if self.mailer is not None:
            self.mailer.smtp = config.smtp
            self.mailer.max_attempts = max(1, config.settings.max_send_attempts)
        log.info(
            "Config loaded: %d active user(s), categories=%s, digest slots=%s",
            len(config.active_users),
            ["ALL" if c is None else c for c in categories_to_fetch(config.active_users)],
            config.digest_slots() or "-",
        )

    def reload_if_changed(self) -> bool:
        """Hot-reload config.json and .env when their mtime changes. Invalid edits are ignored."""
        path = self.config.path
        env_path = self.config.env_path
        try:
            mtime = path.stat().st_mtime
            env_mtime = env_path.stat().st_mtime if (env_path and env_path.is_file()) else 0.0
        except OSError as exc:
            log.error("Config file unavailable (%s); keeping previous config", exc)
            return False
        if mtime == self.config.mtime and env_mtime == self.config.env_mtime:
            return False
        try:
            if env_path and env_mtime != self.config.env_mtime:
                load_env(str(env_path), override=True)
            new_cfg = load_config(path, env_path=env_path)
            log.info("Configuration file changed - reloading")
            self.apply_config(new_cfg)
            return True
        except (ConfigError, OSError) as exc:
            log.error("Config change rejected (%s); keeping previous config", exc)
            self.config = replace(self.config, mtime=mtime, env_mtime=env_mtime)
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error reloading config (%s); keeping previous config", exc)
            return False

    # ------------------------------------------------------------------ ingestion

    def poll(self) -> PollReport:
        users = self.config.active_users
        cats = categories_to_fetch(users)
        if not cats:
            log.warning("No active users - skipping poll")
            return PollReport()
        rules_hash = self.config.rules_hash()
        try:
            full_scan = self.db.get_meta("rules_hash") != rules_hash
        except Exception as exc:  # noqa: BLE001
            log.error("Failed reading rules_hash from DB (%s); defaulting to incremental", exc)
            full_scan = False
        try:
            jobs = self.scraper.fetch(cats, is_known=None if full_scan else self.db.all_known)
        except ScrapeError as exc:
            log.error("Poll fetch failed: %s (will retry next cycle)", exc)
            return PollReport(full_scan=full_scan, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error during scrape (will retry next cycle): %s", exc)
            return PollReport(full_scan=full_scan, error=str(exc))

        try:
            job_list: list[Job] = list(jobs.values())
            new_ids = self.db.upsert_jobs(job_list)
            if full_scan and not getattr(self.scraper, "last_partial_failure", False):
                self.db.clear_pending_notifications()

            report = PollReport(fetched=len(job_list), new_jobs=len(new_ids), full_scan=full_scan)
            for email, results in self.matcher.match_all(job_list).items():
                uid = self.user_ids.get(email)
                if uid is None:
                    self.user_ids = self.db.sync_users(self.config.users)
                    uid = self.user_ids.get(email)
                if uid is not None:
                    report.queued[email] = self.db.enqueue_many(
                        uid, [(r.job, r.matched_keywords) for r in results]
                    )
            if not getattr(self.scraper, "last_partial_failure", False):
                self.db.set_meta("rules_hash", rules_hash)
            self.db.set_meta("last_poll_at", utcnow())
            log.info("Poll complete: %s", report.summary())
            return report
        except Exception as exc:  # noqa: BLE001
            log.exception("Database error during poll (will retry next cycle): %s", exc)
            return PollReport(full_scan=full_scan, error=str(exc))

    # ------------------------------------------------------------------ dispatch

    def _chunks(self, items: Sequence, size: int) -> list[Sequence]:
        size = max(1, size)
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _send(self, user: UserProfile, content: EmailContent, notification_ids: list[int]) -> int:
        assert self.mailer is not None
        try:
            self.mailer.send(user.email, content)
        except MailError as exc:
            if exc.abort_cycle:
                log.error("Dispatch aborted: %s", exc)
                raise _AbortDispatch() from exc
            log.error("Delivery to %s failed: %s", user.email, exc)
            max_attempts = 1 if exc.permanent else self.config.settings.max_send_attempts
            self.db.mark_failed_attempt(notification_ids, str(exc), max_attempts)
            return 0
        self.db.mark_sent(notification_ids)
        return len(notification_ids)

    def _dispatch_user(self, user: UserProfile, digest: bool) -> int:
        s = self.config.settings
        uid = self.user_ids.get(user.email)
        if uid is None:
            self.user_ids = self.db.sync_users(self.config.users)
            uid = self.user_ids.get(user.email)
        if uid is None:
            return 0
        rows = self.db.pending_for_user(uid, s.max_send_attempts)
        if not rows:
            return 0
        items = [AlertItem.from_row(r) for r in rows]
        ids = [r["notification_id"] for r in rows]
        sent = 0
        if not digest and len(items) <= s.instant_batch_threshold:
            for item, nid in zip(items, ids):
                sent += self._send(user, build_instant_email(item), [nid])
            return sent
        label = "digest" if digest else "batch"
        pairs = list(zip(items, ids))
        for chunk in self._chunks(pairs, s.max_jobs_per_email):
            content = build_digest_email([p[0] for p in chunk], label=label)
            sent += self._send(user, content, [p[1] for p in chunk])
        return sent

    def _dispatch(self, users: Sequence[UserProfile], digest: bool) -> int:
        if self.mailer is None or not users:
            return 0
        total = 0
        session_fn = getattr(self.mailer, "session", None)
        ctx = session_fn() if callable(session_fn) else nullcontext()
        try:
            with ctx:
                for user in users:
                    try:
                        total += self._dispatch_user(user, digest)
                    except _AbortDispatch:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        log.exception(
                            "Unexpected error dispatching alerts to %s: %s", user.email, exc
                        )
        except _AbortDispatch:
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in dispatch session: %s", exc)
        return total

    def dispatch_instant(self) -> int:
        users = [u for u in self.config.active_users if u.schedule.mode == "instant"]
        sent = self._dispatch(users, digest=False)
        if sent:
            log.info("Instant dispatch: %d job alert(s) delivered", sent)
        return sent

    def dispatch_digest(self, slot: str | None = None) -> int:
        users = [
            u
            for u in self.config.active_users
            if u.schedule.mode == "digest" and (slot is None or slot in u.schedule.digest_times)
        ]
        sent = self._dispatch(users, digest=True)
        log.info(
            "Digest dispatch (%s): %d job(s) delivered to %d user(s)",
            slot or "manual",
            sent,
            len(users),
        )
        return sent

    # ------------------------------------------------------------------ lifecycle

    def run_cycle(self) -> None:
        try:
            self.poll()
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in poll stage: %s", exc)
        try:
            self.dispatch_instant()
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in instant dispatch stage: %s", exc)

    def maintenance(self) -> None:
        try:
            deleted = self.db.purge_old_jobs(self.config.settings.job_retention_days)
            log.info("Maintenance: purged %d stale job(s); stats=%s", deleted, self.db.stats())
        except Exception as exc:  # noqa: BLE001
            log.exception("Maintenance failed: %s", exc)


def build_engine(config: AppConfig, with_mailer: bool = True) -> AlertEngine:
    db = Database(config.db_path)
    scraper = JobScraper(config.settings)
    mailer = (
        Mailer(config.smtp, max_attempts=config.settings.max_send_attempts) if with_mailer else None
    )
    return AlertEngine(config, db, scraper, mailer)


__all__ = ["AlertEngine", "PollReport", "build_engine", "categories_to_fetch"]
