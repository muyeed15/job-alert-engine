"""Administrative CLI (manage.py) for user management, database maintenance, and inspection."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import (
    CATEGORIES,
    PROJECT_ROOT,
    ConfigError,
    Settings,
    UserProfile,
    _parse_settings,
    _parse_user,
    default_config_path,
    load_env,
    load_smtp_from_env,
)
from .db import Database


def _split_csv(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for item in values:
        for part in str(item).split(","):
            s = part.strip()
            if s:
                out.append(s)
    return out


def _format_user_dict(u: UserProfile) -> dict[str, Any]:
    cats = [CATEGORIES.get(c, c) for c in u.categories]
    sched: dict[str, Any] = {"mode": u.schedule.mode}
    if u.schedule.mode == "digest":
        sched["digest_time"] = (
            u.schedule.digest_times[0]
            if len(u.schedule.digest_times) == 1
            else list(u.schedule.digest_times)
        )
    exp: dict[str, Any] = {
        "min_years": u.experience.min_years,
        "max_years": u.experience.max_years,
    }
    if not u.experience.include_unspecified:
        exp["include_unspecified"] = False
    doc: dict[str, Any] = {
        "name": u.name,
        "email": u.email,
        "categories": cats,
        "locations": list(u.locations),
    }
    if u.exclude_locations:
        doc["exclude_locations"] = list(u.exclude_locations)
    if not u.include_anywhere_in_bd:
        doc["include_anywhere_in_bd"] = False
    doc["experience"] = exp
    doc["keywords"] = list(u.keywords)
    if u.required_keywords:
        doc["required_keywords"] = list(u.required_keywords)
    if u.exclude_keywords:
        doc["exclude_keywords"] = list(u.exclude_keywords)
    doc["keyword_scope"] = u.keyword_scope
    if u.job_types:
        doc["job_types"] = list(u.job_types)
    if u.workplaces:
        doc["workplaces"] = list(u.workplaces)
    if u.min_salary is not None:
        doc["min_salary"] = u.min_salary
    if not u.include_negotiable_salary:
        doc["include_negotiable_salary"] = False
    if u.companies:
        doc["companies"] = list(u.companies)
    if u.exclude_companies:
        doc["exclude_companies"] = list(u.exclude_companies)
    if u.max_age_days is not None:
        doc["max_age_days"] = u.max_age_days
    doc["schedule"] = sched
    if not u.enabled:
        doc["enabled"] = False
    return doc


def _load_raw_config(path: Path) -> tuple[dict[str, Any], Settings, list[UserProfile]]:
    if not path.is_file():
        default_s = Settings()
        raw_settings = asdict(default_s)
        raw_settings.pop("api_urls", None)
        raw_settings.pop("job_url_template", None)
        return {"settings": raw_settings, "users": []}, default_s, []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc
    if isinstance(doc, list):
        doc = {"users": doc}
    if not isinstance(doc, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    settings = _parse_settings(doc.get("settings") or {})
    users_raw = doc.get("users") or []
    if not isinstance(users_raw, list):
        raise ConfigError("'users' must be a list")
    users = [_parse_user(u, i) for i, u in enumerate(users_raw)]
    return doc, settings, users


def _save_config_atomic(path: Path, doc: dict[str, Any], users: list[UserProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(doc)
    if "settings" not in out:
        s = asdict(Settings())
        s.pop("api_urls", None)
        s.pop("job_url_template", None)
        out["settings"] = s
    out["users"] = [_format_user_dict(u) for u in users]
    payload = json.dumps(out, indent=2, ensure_ascii=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def _resolve_db_path(settings: Settings) -> Path:
    override = os.environ.get("DB_PATH")
    p = Path(override or settings.db_path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def _sync_db_users(settings: Settings, users: list[UserProfile], reset_hash: bool = True) -> None:
    db = Database(_resolve_db_path(settings))
    try:
        db.sync_users(users)
        if reset_hash:
            with db.tx() as c:
                c.execute("DELETE FROM notifications WHERE status = 'pending'")
                c.execute("DELETE FROM meta WHERE key = 'rules_hash'")
    finally:
        db.close()


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default != "" else ""
    try:
        val = input(f"{label}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val if val != "" else default


def _confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        ans = input(f"{question} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


# ----------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    env_path = Path(args.env).expanduser() if args.env else PROJECT_ROOT / ".env"
    example_cfg = PROJECT_ROOT / "config.example.json"
    example_env = PROJECT_ROOT / ".env.example"

    if not cfg_path.is_file():
        if example_cfg.is_file():
            shutil.copyfile(example_cfg, cfg_path)
            print(f"Created {cfg_path} from {example_cfg.name}")
        else:
            _save_config_atomic(cfg_path, {}, [])
            print(f"Created empty {cfg_path}")
    else:
        print(f"Config already exists: {cfg_path}")

    if not env_path.is_file() and example_env.is_file():
        shutil.copyfile(example_env, env_path)
        print(f"Created {env_path} from {example_env.name}")
    elif env_path.is_file():
        print(f"Env file already exists: {env_path}")

    _, settings, users = _load_raw_config(cfg_path)
    _sync_db_users(settings, users, reset_hash=False)
    print(f"Database initialized at {_resolve_db_path(settings)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, users = _load_raw_config(cfg_path)
    db_path = _resolve_db_path(settings)
    smtp = load_smtp_from_env()

    db = Database(db_path)
    try:
        st = db.stats()
        last_poll = db.get_meta("last_poll_at") or "never"
    finally:
        db.close()

    size_kb = db_path.stat().st_size / 1024.0 if db_path.is_file() else 0.0
    active_count = sum(1 for u in users if u.enabled)

    print("SYSTEM STATUS")
    print("=" * 72)
    print(f"Config File          : {cfg_path} ({'exists' if cfg_path.is_file() else 'missing'})")
    print(f"Database File        : {db_path} ({size_kb:.1f} KB)")
    print(
        f"SMTP Status          : {'configured' if smtp.configured else 'NOT CONFIGURED'}"
        f" ({smtp.user or 'no user'} via {smtp.host}:{smtp.port} {smtp.security})"
    )
    print(f"Poll Interval        : {settings.poll_interval_minutes} minutes")
    print(f"Configured Users     : {active_count} enabled / {len(users)} total")
    print(f"Stored Jobs          : {st.get('jobs', 0)}")
    print(f"Last Poll (UTC)      : {last_poll}")
    print("-" * 72)
    print(f"Notifications Sent   : {st.get('notifications_sent', 0)}")
    print(f"Notifications Pending: {st.get('notifications_pending', 0)}")
    print(f"Notifications Failed : {st.get('notifications_failed', 0)}")
    print(f"Notifications Skipped: {st.get('notifications_skipped', 0)}")
    print("=" * 72)
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, users = _load_raw_config(cfg_path)
    db_path = _resolve_db_path(settings)

    user_counts: dict[str, dict[str, int]] = {}
    if db_path.is_file():
        db = Database(db_path)
        try:
            rows = db.conn.execute(
                """
                SELECT lower(u.email) AS email, n.status, COUNT(*) AS cnt
                FROM users u JOIN notifications n ON n.user_id = u.id
                GROUP BY lower(u.email), n.status
                """
            ).fetchall()
            for r in rows:
                user_counts.setdefault(r["email"], {})[r["status"]] = r["cnt"]
        finally:
            db.close()

    if not users:
        print("No users configured in config.json.")
        return 0

    print(f"Configured Users ({len(users)}):")
    print("=" * 78)
    for idx, u in enumerate(users, 1):
        state = "ENABLED" if u.enabled else "DISABLED"
        sched = (
            "instant"
            if u.schedule.mode == "instant"
            else f"digest @ {', '.join(u.schedule.digest_times)}"
        )
        cats = (
            ", ".join(f"{CATEGORIES.get(c, c)} ({c})" for c in u.categories)
            if u.categories
            else "ALL"
        )
        locs = ", ".join(u.locations) if u.locations else "ALL"
        if u.locations and not u.include_anywhere_in_bd:
            locs += " (strict, no Anywhere in BD)"
        max_y = "no limit" if u.experience.max_years is None else f"{u.experience.max_years}y"
        exp = (
            f"{u.experience.min_years}y to {max_y} "
            f"(unspecified={'yes' if u.experience.include_unspecified else 'no'})"
        )
        counts = user_counts.get(u.email.lower(), {})
        sent = counts.get("sent", 0)
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)

        print(f"[{idx}] {u.name} <{u.email}>  [{state}]")
        print(f"    Schedule   : {sched}")
        print(f"    Categories : {cats}")
        print(f"    Locations  : {locs}")
        if u.exclude_locations:
            print(f"    Excl. Locs : {', '.join(u.exclude_locations)}")
        print(f"    Experience : {exp}")
        print(
            f"    Keywords   : {', '.join(u.keywords) if u.keywords else '(all in category)'}"
            f" (scope={u.keyword_scope})"
        )
        if u.required_keywords:
            print(f"    Required   : {', '.join(u.required_keywords)}")
        if u.exclude_keywords:
            print(f"    Excludes   : {', '.join(u.exclude_keywords)}")
        if u.job_types:
            print(f"    Job Types  : {', '.join(u.job_types)}")
        if u.workplaces:
            print(f"    Workplaces : {', '.join(u.workplaces)}")
        if u.min_salary is not None:
            print(
                f"    Min Salary : Tk. {u.min_salary} "
                f"(negotiable={'yes' if u.include_negotiable_salary else 'no'})"
            )
        if u.companies:
            print(f"    Companies  : {', '.join(u.companies)}")
        if u.exclude_companies:
            print(f"    Excl. Comp : {', '.join(u.exclude_companies)}")
        if u.max_age_days is not None:
            print(f"    Max Age    : {u.max_age_days} day(s)")
        print(f"    Alerts     : sent={sent}, pending={pending}, failed={failed}")
        print("-" * 78)
    return 0


def _interactive_user_input(existing: UserProfile | None = None) -> dict[str, Any]:
    print("Enter user profile details (press Enter to keep default):")
    name = _prompt("Full Name", existing.name if existing else "")
    email = _prompt("Email Address", existing.email if existing else "")

    default_cats = (
        ", ".join(CATEGORIES.get(c, str(c)) for c in existing.categories)
        if existing and existing.categories
        else "IT/Telecommunication"
    )
    cats_str = _prompt("Categories (comma-separated names or IDs, or ALL)", default_cats)
    cats = [] if cats_str.strip().upper() == "ALL" else _split_csv([cats_str])

    default_locs = ", ".join(existing.locations) if (existing and existing.locations) else "ALL"
    locs_str = _prompt("Locations (e.g. Dhaka, Uttara, Chattogram, or ALL)", default_locs)
    locs = [] if locs_str.strip().upper() == "ALL" else _split_csv([locs_str])

    default_ex_locs = ", ".join(existing.exclude_locations) if existing else ""
    ex_locs_str = _prompt("Exclude locations (e.g. Savar, Gazipur, or blank)", default_ex_locs)

    min_str = _prompt(
        "Minimum experience years", str(existing.experience.min_years if existing else 0)
    )
    default_max = (
        ""
        if (existing and existing.experience.max_years is None)
        else str(existing.experience.max_years if existing else 1)
    )
    max_str = _prompt("Maximum experience years (leave blank or 'null' for no limit)", default_max)
    max_clean = max_str.strip()
    max_val: Any = (
        None
        if max_clean.lower() in ("", "null", "none")
        else (int(max_clean) if max_clean.lstrip("-").isdigit() else max_clean)
    )

    inc_default = "yes" if (not existing or existing.experience.include_unspecified) else "no"
    inc_str = _prompt("Include jobs with unspecified/NA experience? (yes/no)", inc_default)
    inc_unspec = inc_str.strip().lower() in ("y", "yes", "true", "1")

    default_kw = ", ".join(existing.keywords) if existing else ""
    kw_str = _prompt("Include keywords - OR match (comma-separated, blank for all)", default_kw)

    default_req = ", ".join(existing.required_keywords) if existing else ""
    req_str = _prompt(
        "Required keywords - AND match (comma-separated, blank for none)", default_req
    )

    default_ex = ", ".join(existing.exclude_keywords) if existing else ""
    ex_str = _prompt("Exclude title keywords (comma-separated, blank for none)", default_ex)

    scope = _prompt("Keyword scope (all/title)", existing.keyword_scope if existing else "all")

    default_jt = ", ".join(existing.job_types) if (existing and existing.job_types) else "ALL"
    jt_str = _prompt(
        "Job types (FullTime, PartTime, Contract, Intern, Freelance, or ALL)", default_jt
    )
    job_types = [] if jt_str.strip().upper() == "ALL" else _split_csv([jt_str])

    default_wp = ", ".join(existing.workplaces) if (existing and existing.workplaces) else "ALL"
    wp_str = _prompt("Workplaces (office, remote, hybrid, or ALL)", default_wp)
    workplaces = [] if wp_str.strip().upper() == "ALL" else _split_csv([wp_str])

    default_sal = str(existing.min_salary) if (existing and existing.min_salary) else ""
    sal_str = _prompt("Minimum monthly salary in BDT (blank for no minimum)", default_sal)
    sal_clean = sal_str.strip()
    min_sal: Any = (
        None
        if sal_clean.lower() in ("", "null", "none", "0")
        else (int(sal_clean) if sal_clean.lstrip("-").isdigit() else sal_clean)
    )

    mode = _prompt(
        "Schedule mode (instant/digest)", existing.schedule.mode if existing else "instant"
    ).lower()

    digest_times: list[str] = []
    if mode == "digest":
        default_times = (
            ", ".join(existing.schedule.digest_times)
            if (existing and existing.schedule.digest_times)
            else "09:00"
        )
        t_str = _prompt("Digest times (24h HH:MM, comma-separated)", default_times)
        digest_times = _split_csv([t_str])

    min_clean = min_str.strip()
    return {
        "name": name,
        "email": email,
        "categories": cats,
        "locations": locs,
        "exclude_locations": _split_csv([ex_locs_str]),
        "include_anywhere_in_bd": existing.include_anywhere_in_bd if existing else True,
        "experience": {
            "min_years": int(min_clean) if min_clean.lstrip("-").isdigit() else min_clean,
            "max_years": max_val,
            "include_unspecified": inc_unspec,
        },
        "keywords": _split_csv([kw_str]),
        "required_keywords": _split_csv([req_str]),
        "exclude_keywords": _split_csv([ex_str]),
        "keyword_scope": scope,
        "job_types": job_types,
        "workplaces": workplaces,
        "min_salary": min_sal,
        "include_negotiable_salary": existing.include_negotiable_salary if existing else True,
        "companies": list(existing.companies) if existing else [],
        "exclude_companies": list(existing.exclude_companies) if existing else [],
        "max_age_days": existing.max_age_days if existing else None,
        "schedule": {"mode": mode, "digest_time": digest_times},
        "enabled": existing.enabled if existing else True,
    }


def cmd_add_user(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    doc, settings, users = _load_raw_config(cfg_path)

    interactive = args.interactive or (not args.name and not args.email)
    if interactive:
        raw_user = _interactive_user_input()
    else:
        if not args.name or not args.email:
            raise ConfigError(
                "Both --name and --email are required (or run with -i for interactive mode)"
            )
        cats = _split_csv(args.categories)
        if len(cats) == 1 and cats[0].upper() == "ALL":
            cats = []
        locs = _split_csv(args.locations)
        if len(locs) == 1 and locs[0].upper() == "ALL":
            locs = []
        jtypes = _split_csv(args.job_types)
        if len(jtypes) == 1 and jtypes[0].upper() == "ALL":
            jtypes = []
        wplaces = _split_csv(args.workplaces)
        if len(wplaces) == 1 and wplaces[0].upper() == "ALL":
            wplaces = []
        raw_user = {
            "name": args.name,
            "email": args.email,
            "categories": cats,
            "locations": locs,
            "exclude_locations": _split_csv(args.exclude_locations),
            "include_anywhere_in_bd": not args.strict_location,
            "experience": {
                "min_years": args.min_years if args.min_years is not None else 0,
                "max_years": None if args.no_max_years else args.max_years,
                "include_unspecified": not args.exclude_unspecified,
            },
            "keywords": _split_csv(args.keywords),
            "required_keywords": _split_csv(args.required_keywords),
            "exclude_keywords": _split_csv(args.exclude_keywords),
            "keyword_scope": args.scope or "all",
            "job_types": jtypes,
            "workplaces": wplaces,
            "min_salary": args.min_salary,
            "include_negotiable_salary": not args.exclude_negotiable_salary,
            "companies": _split_csv(args.companies),
            "exclude_companies": _split_csv(args.exclude_companies),
            "max_age_days": args.max_age_days,
            "schedule": {
                "mode": args.mode or "instant",
                "digest_time": _split_csv(args.digest_time),
            },
            "enabled": not args.disabled,
        }

    new_user = _parse_user(raw_user, len(users))
    existing_idx = next(
        (i for i, u in enumerate(users) if u.email.lower() == new_user.email.lower()), None
    )
    if existing_idx is not None:
        if not args.upsert:
            raise ConfigError(
                f"User with email {new_user.email} already exists. Use 'edit-user' or pass --upsert."
            )
        users[existing_idx] = new_user
        action = "Updated"
    else:
        users.append(new_user)
        action = "Added"

    _save_config_atomic(cfg_path, doc, users)
    _sync_db_users(settings, users, reset_hash=True)
    print(f"{action} user: {new_user.name} <{new_user.email}>")
    return 0


def cmd_edit_user(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    doc, settings, users = _load_raw_config(cfg_path)
    target_email = args.email.strip().lower()
    idx = next((i for i, u in enumerate(users) if u.email.lower() == target_email), None)
    if idx is None:
        raise ConfigError(f"User not found: {args.email}")

    current = users[idx]
    has_flags = any(
        v is not None and v is not False
        for v in (
            args.name,
            args.new_email,
            args.categories,
            args.locations,
            args.clear_locations,
            args.exclude_locations,
            args.clear_exclude_locations,
            args.strict_location,
            args.allow_anywhere_location,
            args.min_years,
            args.max_years,
            args.no_max_years,
            args.include_unspecified,
            args.exclude_unspecified,
            args.keywords,
            args.clear_keywords,
            args.required_keywords,
            args.clear_required_keywords,
            args.exclude_keywords,
            args.clear_exclude_keywords,
            args.scope,
            args.job_types,
            args.clear_job_types,
            args.workplaces,
            args.clear_workplaces,
            args.min_salary,
            args.no_min_salary,
            args.exclude_negotiable_salary,
            args.include_negotiable_salary,
            args.companies,
            args.clear_companies,
            args.exclude_companies,
            args.clear_exclude_companies,
            args.max_age_days,
            args.no_max_age_days,
            args.mode,
            args.digest_time,
            args.enable,
            args.disable,
        )
    )

    if args.interactive or not has_flags:
        raw_user = _interactive_user_input(current)
    else:

        def _update_list(
            clear_flag: bool,
            new_vals: list[str] | None,
            cur_vals: tuple[Any, ...],
            allow_all: bool = False,
        ) -> list[Any]:
            if clear_flag:
                return []
            if new_vals is not None:
                split_v = _split_csv(new_vals)
                if allow_all and len(split_v) == 1 and split_v[0].upper() == "ALL":
                    return []
                return split_v
            return list(cur_vals)

        cats = _update_list(False, args.categories, current.categories, allow_all=True)
        locs = _update_list(args.clear_locations, args.locations, current.locations, allow_all=True)
        ex_locs = _update_list(
            args.clear_exclude_locations, args.exclude_locations, current.exclude_locations
        )

        inc_anywhere = current.include_anywhere_in_bd
        if args.strict_location:
            inc_anywhere = False
        elif args.allow_anywhere_location:
            inc_anywhere = True

        max_y = current.experience.max_years
        if args.no_max_years:
            max_y = None
        elif args.max_years is not None:
            max_y = args.max_years

        inc_unspec = current.experience.include_unspecified
        if args.include_unspecified:
            inc_unspec = True
        elif args.exclude_unspecified:
            inc_unspec = False

        kw = _update_list(args.clear_keywords, args.keywords, current.keywords)
        req_kw = _update_list(
            args.clear_required_keywords, args.required_keywords, current.required_keywords
        )
        ex_kw = _update_list(
            args.clear_exclude_keywords, args.exclude_keywords, current.exclude_keywords
        )
        jtypes = _update_list(
            args.clear_job_types, args.job_types, current.job_types, allow_all=True
        )
        wplaces = _update_list(
            args.clear_workplaces, args.workplaces, current.workplaces, allow_all=True
        )
        comps = _update_list(args.clear_companies, args.companies, current.companies)
        ex_comps = _update_list(
            args.clear_exclude_companies, args.exclude_companies, current.exclude_companies
        )

        min_sal = current.min_salary
        if args.no_min_salary:
            min_sal = None
        elif args.min_salary is not None:
            min_sal = args.min_salary

        inc_neg = current.include_negotiable_salary
        if args.exclude_negotiable_salary:
            inc_neg = False
        elif args.include_negotiable_salary:
            inc_neg = True

        max_age = current.max_age_days
        if args.no_max_age_days:
            max_age = None
        elif args.max_age_days is not None:
            max_age = args.max_age_days

        mode = args.mode or current.schedule.mode
        d_times = (
            _split_csv(args.digest_time)
            if args.digest_time is not None
            else list(current.schedule.digest_times)
        )

        enabled = current.enabled
        if args.enable:
            enabled = True
        elif args.disable:
            enabled = False

        raw_user = {
            "name": args.name or current.name,
            "email": args.new_email or current.email,
            "categories": cats,
            "locations": locs,
            "exclude_locations": ex_locs,
            "include_anywhere_in_bd": inc_anywhere,
            "experience": {
                "min_years": (
                    args.min_years if args.min_years is not None else current.experience.min_years
                ),
                "max_years": max_y,
                "include_unspecified": inc_unspec,
            },
            "keywords": kw,
            "required_keywords": req_kw,
            "exclude_keywords": ex_kw,
            "keyword_scope": args.scope or current.keyword_scope,
            "job_types": jtypes,
            "workplaces": wplaces,
            "min_salary": min_sal,
            "include_negotiable_salary": inc_neg,
            "companies": comps,
            "exclude_companies": ex_comps,
            "max_age_days": max_age,
            "schedule": {"mode": mode, "digest_time": d_times},
            "enabled": enabled,
        }

    updated = _parse_user(raw_user, idx)
    for i, u in enumerate(users):
        if i != idx and u.email.lower() == updated.email.lower():
            raise ConfigError(f"Another user already uses email {updated.email}")

    users[idx] = updated
    _save_config_atomic(cfg_path, doc, users)
    _sync_db_users(settings, users, reset_hash=True)
    print(f"Updated user: {updated.name} <{updated.email}>")
    return 0


def cmd_remove_user(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    doc, settings, users = _load_raw_config(cfg_path)
    target = args.email.strip().lower()
    kept = [u for u in users if u.email.lower() != target]
    if len(kept) == len(users):
        raise ConfigError(f"User not found in config: {args.email}")

    if not _confirm(f"Remove user '{args.email}' and delete their notification history?", args.yes):
        print("Aborted.")
        return 1

    _save_config_atomic(cfg_path, doc, kept)
    db = Database(_resolve_db_path(settings))
    try:
        db.delete_user(target)
        db.sync_users(kept)
    finally:
        db.close()
    print(f"Removed user: {args.email}")
    return 0


def cmd_toggle_user(args: argparse.Namespace, enable: bool) -> int:
    cfg_path = args.config or default_config_path()
    doc, settings, users = _load_raw_config(cfg_path)
    target = args.email.strip().lower()
    idx = next((i for i, u in enumerate(users) if u.email.lower() == target), None)
    if idx is None:
        raise ConfigError(f"User not found: {args.email}")

    u = users[idx]
    raw = _format_user_dict(u)
    raw["enabled"] = enable
    users[idx] = _parse_user(raw, idx)
    _save_config_atomic(cfg_path, doc, users)
    _sync_db_users(settings, users, reset_hash=enable)
    state = "enabled" if enable else "disabled"
    print(f"User {u.email} is now {state}.")
    return 0


def cmd_reset_user(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, _ = _load_raw_config(cfg_path)
    target = args.email.strip().lower()
    if not _confirm(f"Clear all notification history for '{target}'?", args.yes):
        print("Aborted.")
        return 1
    db = Database(_resolve_db_path(settings))
    try:
        removed = db.clear_user_notifications(target)
    finally:
        db.close()
    print(f"Cleared {removed} notification record(s) for {target}.")
    return 0


def cmd_clear_db(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, users = _load_raw_config(cfg_path)
    db_path = _resolve_db_path(settings)
    if not db_path.is_file():
        print(f"Database file does not exist: {db_path}")
        return 0

    scope = "notifications only" if args.notifications_only else "all jobs and notifications"
    if not _confirm(f"Clear {scope} from {db_path}?", args.yes):
        print("Aborted.")
        return 1

    db = Database(db_path)
    try:
        res = db.clear_database(notifications_only=args.notifications_only)
        db.sync_users(users)
    finally:
        db.close()
    print(
        f"Database cleared: jobs_deleted={res['jobs_deleted']}, "
        f"notifications_deleted={res['notifications_deleted']}"
    )
    return 0


def cmd_retry_failed(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, _ = _load_raw_config(cfg_path)
    db = Database(_resolve_db_path(settings))
    try:
        reset_count = db.retry_failed_notifications(args.user)
    finally:
        db.close()
    print(f"Reset {reset_count} failed notification(s) back to pending.")
    return 0


def cmd_purge_old(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, _ = _load_raw_config(cfg_path)
    days = args.days if args.days is not None else settings.job_retention_days
    if days < 1:
        raise ConfigError("--days must be >= 1")
    db = Database(_resolve_db_path(settings))
    try:
        deleted = db.purge_old_jobs(days)
    finally:
        db.close()
    print(f"Purged {deleted} job(s) not seen in the last {days} day(s).")
    return 0


def cmd_list_jobs(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, _ = _load_raw_config(cfg_path)
    db = Database(_resolve_db_path(settings))
    limit = max(1, args.limit)
    try:
        if args.search:
            q = f"%{args.search.strip()}%"
            rows = db.conn.execute(
                """
                SELECT * FROM jobs
                WHERE title LIKE ? OR company LIKE ? OR location LIKE ?
                ORDER BY COALESCE(posted_at, discovered_at) DESC
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        else:
            rows = db.conn.execute(
                "SELECT * FROM jobs ORDER BY COALESCE(posted_at, discovered_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        db.close()

    if not rows:
        print("No matching jobs in database.")
        return 0

    print(f"Showing {len(rows)} job(s):")
    print("=" * 78)
    for r in rows:
        print(f"[{r['job_id']}] {r['title']} - {r['company'] or 'N/A'}")
        print(
            f"    Location: {r['location'] or 'N/A'} | Experience: {r['experience'] or 'N/A'}"
            f" | Deadline: {r['deadline'] or 'N/A'}"
        )
        print(f"    URL: {r['url']}")
    return 0


def cmd_list_notifications(args: argparse.Namespace) -> int:
    cfg_path = args.config or default_config_path()
    _, settings, _ = _load_raw_config(cfg_path)
    db = Database(_resolve_db_path(settings))
    limit = max(1, args.limit)
    clauses: list[str] = []
    params: list[Any] = []
    if args.user:
        clauses.append("lower(u.email) = ?")
        params.append(args.user.strip().lower())
    if args.status:
        clauses.append("n.status = ?")
        params.append(args.status.strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    try:
        rows = db.conn.execute(
            f"""
            SELECT n.id, n.job_id, n.status, n.matched, n.attempts, n.last_error,
                   n.created_at, n.sent_at, u.email, j.title, j.company
            FROM notifications n
            JOIN users u ON u.id = n.user_id
            JOIN jobs j ON j.job_id = n.job_id
            {where}
            ORDER BY n.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        db.close()

    if not rows:
        print("No notifications found.")
        return 0

    print(f"Showing {len(rows)} notification(s):")
    print("=" * 78)
    for r in rows:
        ts = r["sent_at"] or r["created_at"]
        print(
            f"#{r['id']} [{r['status'].upper()}] {r['email']} | Job {r['job_id']}: "
            f"{r['title']} ({r['company'] or 'N/A'})"
        )
        print(f"    Time: {ts} | Matched: {r['matched'] or '-'} | Attempts: {r['attempts']}")
        if r["last_error"]:
            print(f"    Error: {r['last_error']}")
    return 0


def cmd_categories() -> int:
    for cid, name in CATEGORIES.items():
        print(f"{cid:>3}  {name}")
    return 0


# ----------------------------------------------------------------------------- parser


def _add_user_filter_args(p: argparse.ArgumentParser, for_edit: bool = False) -> None:
    p.add_argument("-i", "--interactive", action="store_true", help="prompt interactively")
    p.add_argument("--name", help="user full name")
    if for_edit:
        p.add_argument("--new-email", help="change recipient email address")
    else:
        p.add_argument("--email", help="recipient email address")
        p.add_argument("--upsert", action="store_true", help="update user if email already exists")
        p.add_argument("--disabled", action="store_true", help="create user in disabled state")

    p.add_argument(
        "--categories",
        nargs="+",
        help="category names or IDs (comma-separated or space-separated, or ALL)",
    )
    p.add_argument(
        "--locations",
        nargs="+",
        help="target locations (e.g. Dhaka, Uttara, Chattogram, or ALL)",
    )
    if for_edit:
        p.add_argument("--clear-locations", action="store_true", help="match all locations")
    p.add_argument(
        "--exclude-locations",
        nargs="+",
        help="locations to exclude (e.g. Savar, Gazipur)",
    )
    if for_edit:
        p.add_argument(
            "--clear-exclude-locations", action="store_true", help="clear excluded locations"
        )
    p.add_argument(
        "--strict-location",
        action="store_true",
        help="do not match 'Anywhere in Bangladesh' when locations are set",
    )
    if for_edit:
        p.add_argument(
            "--allow-anywhere-location",
            action="store_true",
            help="allow 'Anywhere in Bangladesh' when locations are set",
        )

    p.add_argument("--min-years", type=int, help="minimum years of experience")
    p.add_argument("--max-years", type=int, help="maximum years of experience")
    p.add_argument(
        "--no-max-years", action="store_true", help="set max_years to null (no upper limit)"
    )
    if for_edit:
        p.add_argument(
            "--include-unspecified",
            action="store_true",
            help="include jobs with NA/unspecified experience",
        )
    p.add_argument(
        "--exclude-unspecified",
        action="store_true",
        help="exclude jobs with NA/unspecified experience",
    )
    p.add_argument("--keywords", nargs="+", help="include keywords - OR match (comma-separated)")
    if for_edit:
        p.add_argument("--clear-keywords", action="store_true", help="clear all include keywords")
    p.add_argument(
        "--required-keywords", nargs="+", help="required keywords - AND match (comma-separated)"
    )
    if for_edit:
        p.add_argument(
            "--clear-required-keywords", action="store_true", help="clear required keywords"
        )
    p.add_argument(
        "--exclude-keywords",
        nargs="+",
        help="exclude title keywords (comma-separated)",
    )
    if for_edit:
        p.add_argument(
            "--clear-exclude-keywords", action="store_true", help="clear all exclude keywords"
        )
    p.add_argument("--scope", choices=("all", "title"), help="keyword match scope")

    p.add_argument(
        "--job-types",
        nargs="+",
        help="FullTime, PartTime, Contract, Intern, Freelance (or ALL)",
    )
    if for_edit:
        p.add_argument("--clear-job-types", action="store_true", help="match all job types")

    p.add_argument(
        "--workplaces",
        nargs="+",
        help="office, remote, hybrid (or ALL)",
    )
    if for_edit:
        p.add_argument("--clear-workplaces", action="store_true", help="match all workplaces")

    p.add_argument("--min-salary", type=int, help="minimum monthly salary in BDT")
    if for_edit:
        p.add_argument("--no-min-salary", action="store_true", help="remove minimum salary")
        p.add_argument(
            "--include-negotiable-salary",
            action="store_true",
            help="include negotiable/undisclosed salary when min-salary is set",
        )
    p.add_argument(
        "--exclude-negotiable-salary",
        action="store_true",
        help="reject negotiable/undisclosed salary when min-salary is set",
    )

    p.add_argument("--companies", nargs="+", help="only match these company names")
    if for_edit:
        p.add_argument("--clear-companies", action="store_true", help="clear company filter")
    p.add_argument("--exclude-companies", nargs="+", help="exclude these company names")
    if for_edit:
        p.add_argument(
            "--clear-exclude-companies", action="store_true", help="clear excluded companies"
        )

    p.add_argument("--max-age-days", type=int, help="only match jobs posted within the last N days")
    if for_edit:
        p.add_argument("--no-max-age-days", action="store_true", help="remove max age limit")

    p.add_argument("--mode", choices=("instant", "digest"), help="delivery schedule mode")
    p.add_argument(
        "--digest-time",
        nargs="+",
        help="digest times in HH:MM 24h format (when mode is digest)",
    )
    if for_edit:
        p.add_argument("--enable", action="store_true", help="enable this user")
        p.add_argument("--disable", action="store_true", help="disable this user")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manage.py",
        description="Administrative CLI for Job Alert Engine (users, database, and inspection).",
    )
    p.add_argument("--config", type=Path, help="path to config.json (default: ./config.json)")
    p.add_argument("--env", help="path to .env file (default: ./.env)")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialize config.json, .env, and database")
    sub.add_parser("status", aliases=["stats"], help="show system and database status")
    sub.add_parser("list-users", aliases=["users"], help="list all configured users")
    sub.add_parser("categories", help="list all valid category IDs and names")

    p_add = sub.add_parser("add-user", help="create and register a new user profile")
    _add_user_filter_args(p_add, for_edit=False)

    p_edit = sub.add_parser("edit-user", help="modify an existing user profile")
    p_edit.add_argument("email", help="email address of the user to edit")
    _add_user_filter_args(p_edit, for_edit=True)

    p_rm = sub.add_parser(
        "remove-user", aliases=["delete-user"], help="delete a user from config and database"
    )
    p_rm.add_argument("email", help="email address of the user to remove")
    p_rm.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")

    p_en = sub.add_parser("enable-user", help="enable notifications for a user")
    p_en.add_argument("email", help="email address of the user")

    p_dis = sub.add_parser("disable-user", help="pause notifications for a user")
    p_dis.add_argument("email", help="email address of the user")

    p_rst = sub.add_parser(
        "reset-user", help="clear notification history for a user so jobs can re-match"
    )
    p_rst.add_argument("email", help="email address of the user")
    p_rst.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")

    p_clr = sub.add_parser("clear-db", help="clear jobs and notifications from the database")
    p_clr.add_argument(
        "--notifications-only",
        action="store_true",
        help="keep stored jobs and only clear notification history",
    )
    p_clr.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")

    p_retry = sub.add_parser("retry-failed", help="reset failed notifications back to pending")
    p_retry.add_argument("--user", help="only reset failed notifications for this user email")

    p_purge = sub.add_parser("purge-old", help="delete stale jobs older than N days")
    p_purge.add_argument("--days", type=int, help="retention days (default from settings)")

    p_jobs = sub.add_parser("list-jobs", help="list stored jobs in the database")
    p_jobs.add_argument("--limit", type=int, default=20, help="max rows to display (default 20)")
    p_jobs.add_argument("--search", help="filter by title, company, or location substring")

    p_notifs = sub.add_parser("list-notifications", help="list notification records")
    p_notifs.add_argument("--user", help="filter by user email")
    p_notifs.add_argument(
        "--status", choices=("pending", "sent", "failed", "skipped"), help="filter by status"
    )
    p_notifs.add_argument("--limit", type=int, default=25, help="max rows to display (default 25)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env(args.env)
    cmd = args.command
    try:
        if cmd == "init":
            return cmd_init(args)
        if cmd in ("status", "stats"):
            return cmd_status(args)
        if cmd in ("list-users", "users"):
            return cmd_list_users(args)
        if cmd == "categories":
            return cmd_categories()
        if cmd == "add-user":
            return cmd_add_user(args)
        if cmd == "edit-user":
            return cmd_edit_user(args)
        if cmd in ("remove-user", "delete-user"):
            return cmd_remove_user(args)
        if cmd == "enable-user":
            return cmd_toggle_user(args, True)
        if cmd == "disable-user":
            return cmd_toggle_user(args, False)
        if cmd == "reset-user":
            return cmd_reset_user(args)
        if cmd == "clear-db":
            return cmd_clear_db(args)
        if cmd == "retry-failed":
            return cmd_retry_failed(args)
        if cmd == "purge-old":
            return cmd_purge_old(args)
        if cmd == "list-jobs":
            return cmd_list_jobs(args)
        if cmd == "list-notifications":
            return cmd_list_notifications(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"File I/O error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
