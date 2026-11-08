"""Daemon loop built on the ``schedule`` library."""

from __future__ import annotations

import gc
import logging
import resource
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

import schedule

from .engine import AlertEngine

log = logging.getLogger(__name__)

MAINTENANCE_TIME = "03:17"
MAX_SLEEP_SECONDS = 30


def memory_usage_mb() -> tuple[float, float]:
    """Return (current RSS, peak RSS) in MB. Current RSS is available on Linux (/proc)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    current_mb = float("nan")
    try:
        with open("/proc/self/statm") as fh:
            pages = int(fh.read().split()[1])
        current_mb = pages * resource.getpagesize() / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return current_mb, peak_mb


class ServiceScheduler:
    def __init__(self, engine: AlertEngine):
        self.engine = engine
        self.sched = schedule.Scheduler()
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self._wake_event = threading.Event()
        self._interval = 0
        self._digest_slots: list[str] = []
        self._last_signal = 0

    # ------------------------------------------------------------------ helpers

    def _safe(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Scheduled task %s failed", getattr(fn, "__name__", fn))
        finally:
            gc.collect()

    def _install_jobs(self) -> None:
        settings = self.engine.config.settings
        if settings.poll_interval_minutes != self._interval:
            self.sched.clear("poll")
            self._interval = settings.poll_interval_minutes
            self.sched.every(self._interval).minutes.do(self._safe, self._cycle).tag("poll")
            log.info("Polling every %d minute(s)", self._interval)
        slots = self.engine.config.digest_slots()
        if slots != self._digest_slots:
            self.sched.clear("digest")
            self._digest_slots = list(slots)
            for slot in slots:
                self.sched.every().day.at(slot).do(
                    self._safe, self.engine.dispatch_digest, slot
                ).tag("digest")
                log.info("Digest scheduled daily at %s (local time)", slot)

    def _cycle(self) -> None:
        if self.engine.reload_if_changed():
            self._install_jobs()
        self.engine.run_cycle()
        current, peak = memory_usage_mb()
        rss = "n/a" if current != current else f"{current:.1f}MB"
        log.info("Memory: rss=%s peak=%.1fMB", rss, peak)

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        self._last_signal = signum
        self.stop_event.set()
        self._wake_event.set()

    def _handle_hup(self, _signum: int, _frame: Any) -> None:
        self.reload_event.set()
        self._wake_event.set()

    def _reload(self) -> None:
        log.info("SIGHUP received - checking configuration")
        if self.engine.reload_if_changed():
            self._install_jobs()

    # ------------------------------------------------------------------ main loop

    def run(self) -> None:
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGTERM, self._handle_signal)
                signal.signal(signal.SIGINT, self._handle_signal)
                if hasattr(signal, "SIGHUP"):
                    signal.signal(signal.SIGHUP, self._handle_hup)
            except (ValueError, OSError):
                pass

        self._install_jobs()
        self.sched.every().day.at(MAINTENANCE_TIME).do(self._safe, self.engine.maintenance).tag(
            "maintenance"
        )

        log.info("Scheduler started - running initial discovery pass")
        self._safe(self._cycle)

        while not self.stop_event.is_set():
            try:
                if self.reload_event.is_set():
                    self.reload_event.clear()
                    self._safe(self._reload)
                self.sched.run_pending()
                if self.stop_event.is_set():
                    break
                idle = self.sched.idle_seconds
                wait = MAX_SLEEP_SECONDS if idle is None else max(1.0, min(MAX_SLEEP_SECONDS, idle))
                self._wake_event.wait(wait)
                self._wake_event.clear()
            except Exception as exc:  # noqa: BLE001
                log.exception("Unexpected error in scheduler main loop: %s", exc)
                self._wake_event.wait(5.0)
                self._wake_event.clear()

        if self._last_signal:
            try:
                sig_name = signal.Signals(self._last_signal).name
            except ValueError:
                sig_name = str(self._last_signal)
            log.info("Received %s - shutting down", sig_name)
        log.info("Scheduler stopped")
