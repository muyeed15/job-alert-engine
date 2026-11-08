"""Command-line interface.

python -m job_notifier --test-fetch            # one scrape pass, print matches, no emails
python -m job_notifier --test-email you@x.com  # verify SMTP with a sample email
python -m job_notifier --run                   # start the scheduler daemon
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .config import (
    CATEGORIES,
    EMAIL_RE,
    ConfigError,
    load_config,
    load_env,
    load_smtp_from_env,
)
from .engine import build_engine, categories_to_fetch
from .mailer import Mailer, MailError
from .matcher import Matcher
from .scraper import JobScraper, ScrapeError
from .templates import build_digest_email, build_instant_email, sample_items

log = logging.getLogger("job_notifier")


def setup_logging(level: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:  # noqa: BLE001
            pass
    under_journal = bool(os.environ.get("JOURNAL_STREAM") or os.environ.get("INVOCATION_ID"))
    fmt = (
        "%(levelname)s %(name)s: %(message)s"
        if under_journal
        else "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), format=fmt, stream=sys.stdout
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="job-notifier",
        description="Job Alert Engine - headless job monitoring and email alert service.",
    )
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--run", action="store_true", help="start the continuous scheduler daemon")
    mode.add_argument(
        "--test-fetch",
        action="store_true",
        help="run one scraper pass and print matches (no emails)",
    )
    mode.add_argument(
        "--test-email", metavar="RECIPIENT", help="send a sample email to verify SMTP"
    )
    mode.add_argument(
        "--send-digest-now",
        action="store_true",
        help="poll once, then send pending digests immediately",
    )
    mode.add_argument("--list-categories", action="store_true", help="print category IDs and names")
    mode.add_argument("--stats", action="store_true", help="print database statistics")
    mode.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    p.add_argument(
        "--config",
        type=Path,
        help="path to config.json (default: ./config.json or $CONFIG_PATH)",
    )
    p.add_argument("--env", help="path to .env file (default: project .env)")
    p.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="DEBUG, INFO, WARNING, ERROR",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=25,
        help="--test-fetch: max matches printed per user (default 25)",
    )
    p.add_argument("--json", action="store_true", help="--test-fetch: machine-readable JSON output")
    p.add_argument(
        "--template",
        choices=("instant", "digest"),
        default="instant",
        help="--test-email: which template to send (default instant)",
    )
    return p


# ----------------------------------------------------------------------------- commands


def cmd_list_categories() -> int:
    for cid, name in CATEGORIES.items():
        print(f"{cid:>3}  {name}")
    return 0


def cmd_test_fetch(args: argparse.Namespace, env_path: Path) -> int:
    cfg = load_config(args.config, env_path=env_path)
    users = cfg.active_users
    scraper = JobScraper(cfg.settings)
    try:
        jobs = scraper.fetch(categories_to_fetch(users))
    finally:
        scraper.close()
    results = Matcher(users).match_all(list(jobs.values()))

    limit = max(1, args.limit)
    if args.json:
        out = {
            "fetched": len(jobs),
            "users": {
                email: [
                    {
                        "job_id": r.job.job_id,
                        "title": r.job.title,
                        "company": r.job.company,
                        "experience": r.job.experience,
                        "location": r.job.location,
                        "deadline": r.job.deadline,
                        "url": r.job.url,
                        "matched": list(r.matched_keywords),
                    }
                    for r in matches[:limit]
                ]
                for email, matches in results.items()
            },
        }
        print(json.dumps(out, indent=2, ensure_ascii=True))
        return 0

    print(f"\nFetched {len(jobs)} unique listing(s). No emails were sent.\n")
    for user in users:
        matches = results.get(user.email, [])
        print("=" * 78)
        print(f"{user.name} <{user.email}>  [{user.schedule.mode}]  {len(matches)} match(es)")
        print("=" * 78)
        for r in matches[:limit]:
            j = r.job
            print(f"* {j.title}")
            print(f"    {j.company or 'N/A'}  |  {j.location or 'N/A'}")
            print(
                f"    Experience: {j.experience or 'N/A'} ({r.experience.describe()})"
                f"  |  Deadline: {j.deadline or 'N/A'}"
            )
            if r.matched_keywords:
                print(f"    Matched: {', '.join(r.matched_keywords)}")
            print(f"    {j.url}")
        if len(matches) > limit:
            print(f"  ... and {len(matches) - limit} more (use --limit)")
        print()
    return 0


def cmd_test_email(args: argparse.Namespace) -> int:
    recipient = args.test_email.strip()
    if not EMAIL_RE.match(recipient):
        print(f"Invalid recipient: {recipient}", file=sys.stderr)
        return 2
    smtp = load_smtp_from_env()
    if not smtp.configured:
        print("GMAIL_USER and GMAIL_APP_PASSWORD must be set (see .env.example).", file=sys.stderr)
        return 2
    items = sample_items()
    content = (
        build_instant_email(items[0]) if args.template == "instant" else build_digest_email(items)
    )
    content = type(content)("[TEST] " + content.subject, content.text, content.html)
    print(
        f"Sending {args.template} test email via {smtp.host}:{smtp.port}"
        f" ({smtp.security}) as {smtp.user} ..."
    )
    try:
        Mailer(smtp, max_attempts=3).send(recipient, content)
    except MailError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK - test email sent to {recipient}")
    return 0


def cmd_run(args: argparse.Namespace, env_path: Path) -> int:
    from .scheduler import ServiceScheduler

    cfg = load_config(args.config, env_path=env_path)
    if not cfg.smtp.configured:
        log.warning("SMTP credentials missing - matches will queue but no emails can be sent")
    engine = build_engine(cfg)
    log.info("Job Alert Engine %s starting (db=%s)", __version__, cfg.db_path)
    try:
        ServiceScheduler(engine).run()
    finally:
        engine.scraper.close()
        engine.db.close()
    return 0


def cmd_send_digest_now(args: argparse.Namespace, env_path: Path) -> int:
    cfg = load_config(args.config, env_path=env_path)
    engine = build_engine(cfg)
    try:
        engine.poll()
        engine.dispatch_digest(None)
    finally:
        engine.scraper.close()
        engine.db.close()
    return 0


def cmd_stats(args: argparse.Namespace, env_path: Path) -> int:
    from .db import Database

    cfg = load_config(args.config, require_users=False, env_path=env_path)
    db = Database(cfg.db_path)
    try:
        stats = db.stats()
        stats["last_poll_at"] = db.get_meta("last_poll_at")
        print(json.dumps(stats, indent=2))
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    has_mode = any(
        (
            args.run,
            args.test_fetch,
            args.test_email,
            args.send_digest_now,
            args.list_categories,
            args.stats,
        )
    )
    if not has_mode:
        if os.environ.get("PM2_HOME") or os.environ.get("pm_id") is not None:
            args.run = True
        else:
            parser.error(
                "one of the arguments --run --test-fetch --test-email "
                "--send-digest-now --list-categories --stats is required"
            )
    env_path = load_env(args.env)
    setup_logging(args.log_level)
    try:
        if args.list_categories:
            return cmd_list_categories()
        if args.test_email:
            return cmd_test_email(args)
        if args.test_fetch:
            return cmd_test_fetch(args, env_path)
        if args.send_digest_now:
            return cmd_send_digest_now(args, env_path)
        if args.stats:
            return cmd_stats(args, env_path)
        if args.run:
            return cmd_run(args, env_path)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    except ScrapeError as exc:
        log.error("Fetch failed: %s", exc)
        return 1
    except MailError as exc:
        log.error("Email delivery error: %s", exc)
        return 1
    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
        return 1
    except OSError as exc:
        log.error("System I/O error: %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
