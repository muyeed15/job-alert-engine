"""Configuration loading and validation.

* Secrets and SMTP settings are read from environment variables (optionally loaded from ``.env``).
* User profiles and engine settings are read from ``config.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Default upstream JSON search endpoints (first working endpoint is used).
DEFAULT_API_URLS: tuple[str, ...] = (
    "https://api.bdjobs.com/jobs/api/v1/JobSearch/GetJobSearch",
    "https://gateway.bdjobs.com/ActtivejobsTest/api/JobSearch/GetJobSearch",
)
DEFAULT_JOB_URL_TEMPLATE = "https://bdjobs.com/h/details/{job_id}"

# Standard category IDs (1-30: functional, 61-92: skilled trades, -10/-11: others).
CATEGORIES: dict[int, str] = {
    1: "Accounting/Finance",
    2: "Bank/Non-Bank Fin. Institution",
    3: "Supply Chain/Procurement",
    4: "Education/Training",
    5: "Engineer/Architect",
    6: "Garments/Textile",
    7: "General Management/Admin",
    8: "IT/Telecommunication",
    9: "Marketing/Sales",
    10: "Media/Advertisement/Event Mgt.",
    11: "Healthcare/Medical",
    12: "NGO/Development",
    13: "Research/Consultancy",
    14: "Receptionist/PS",
    15: "Data Entry/Operator/BPO",
    16: "Customer Service/Call Centre",
    17: "HR/Org. Development",
    18: "Design/Creative",
    19: "Production/Operation",
    20: "Hospitality/Travel/Tourism",
    21: "Beauty Care/Health & Fitness",
    22: "Law/Legal",
    23: "Electrician/Construction/Repair",
    24: "Security/Support Service",
    25: "Driving/Motor Technician",
    26: "Agro (Plant/Animal/Fisheries)",
    27: "Commercial",
    28: "Company Secretary/Regulatory affairs",
    29: "Pharmaceutical",
    30: "E-commerce/Digital Marketing",
    -10: "Others (General)",
    61: "Data Entry/Computer Operator",
    62: "Mechanic/Technician",
    63: "Nurse",
    64: "Waiter/Waitress",
    65: "Pathologist/Lab Assistant",
    66: "Electrician/Electronics Technician",
    67: "Driver",
    68: "Chef/Cook",
    69: "Housekeeper",
    70: "Security Guard",
    71: "Graphic Designer",
    72: "Welder",
    73: "Plumber/Pipe fitting",
    74: "Sewing machine operator",
    75: "Mason/Construction worker",
    76: "CAD Operator",
    77: "Delivery Man",
    78: "Garments technician/Machine operator",
    79: "Peon",
    80: "Cleaner",
    81: "Gardener",
    82: "Carpenter",
    83: "Showroom Assistant/Salesman",
    84: "Sales Representative (SR)",
    85: "Imam/Khatib/Muezzin",
    86: "Gym/Fitness Trainer",
    87: "Interpreter",
    88: "Beautician/Salon worker",
    89: "Fire Safety/Firefighter",
    90: "Boiler Operator",
    91: "Caregiver/Nanny",
    92: "Physiotherapist",
    -11: "Other Special Skilled Jobs",
}

_CATEGORY_ALIASES: dict[str, int] = {
    "IT": 8,
    "Software": 8,
    "Telecommunication": 8,
    "IT/Telecom": 8,
    "Marketing": 9,
    "Sales": 9,
    "Accounting": 1,
    "Finance": 1,
    "Bank": 2,
    "Banking": 2,
    "Supply Chain": 3,
    "Procurement": 3,
    "Education": 4,
    "Training": 4,
    "Engineering": 5,
    "Engineer": 5,
    "Architect": 5,
    "Garments": 6,
    "Textile": 6,
    "Admin": 7,
    "General Management": 7,
    "Media": 10,
    "Advertisement": 10,
    "Medical": 11,
    "Healthcare": 11,
    "Medical/Pharma": 11,
    "NGO": 12,
    "Development": 12,
    "Research": 13,
    "Consultancy": 13,
    "Receptionist": 14,
    "BPO": 15,
    "Data Entry": 15,
    "Customer Service": 16,
    "Call Centre": 16,
    "Call Center": 16,
    "HR": 17,
    "Human Resources": 17,
    "Design": 18,
    "Creative": 18,
    "Production": 19,
    "Operation": 19,
    "Hospitality": 20,
    "Travel": 20,
    "Tourism": 20,
    "Beauty Care": 21,
    "Fitness": 21,
    "Law": 22,
    "Legal": 22,
    "Electrician": 23,
    "Construction": 23,
    "Security": 24,
    "Driving": 25,
    "Agro": 26,
    "Agriculture": 26,
    "Commercial": 27,
    "Company Secretary": 28,
    "Regulatory Affairs": 28,
    "Pharma": 29,
    "Pharmaceuticals": 29,
    "Digital Marketing": 30,
    "E-commerce": 30,
    "Ecommerce": 30,
    "Others": -10,
}

JOB_TYPE_MAP: dict[str, str] = {
    "fulltime": "FullTime",
    "full-time": "FullTime",
    "full time": "FullTime",
    "full_time": "FullTime",
    "parttime": "PartTime",
    "part-time": "PartTime",
    "part time": "PartTime",
    "part_time": "PartTime",
    "contract": "Contract",
    "contractual": "Contract",
    "intern": "Intern",
    "internship": "Intern",
    "freelance": "Freelance",
    "freelancer": "Freelance",
}

WORKPLACE_MAP: dict[str, str] = {
    "office": "office",
    "onsite": "office",
    "on-site": "office",
    "work at office": "office",
    "remote": "remote",
    "home": "remote",
    "wfh": "remote",
    "work from home": "remote",
    "hybrid": "hybrid",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


_CATEGORY_LOOKUP: dict[str, int] = {_norm(v): k for k, v in CATEGORIES.items()}
_CATEGORY_LOOKUP.update({_norm(k): v for k, v in _CATEGORY_ALIASES.items()})


class ConfigError(ValueError):
    """Raised for any invalid configuration."""


def resolve_category(value: Any) -> int:
    """Resolve a category name, alias, or numeric ID to a category ID."""
    if isinstance(value, bool):
        raise ConfigError(f"Invalid category: {value!r}")
    if isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip())):
        cat_id = int(value)
        if cat_id == 0 or (cat_id < 0 and cat_id not in CATEGORIES):
            raise ConfigError(f"Invalid category ID: {value!r}")
        if cat_id not in CATEGORIES:
            log.warning("Category ID %s is not in the standard list; using it anyway.", cat_id)
        return cat_id
    if isinstance(value, str):
        hit = _CATEGORY_LOOKUP.get(_norm(value))
        if hit is not None:
            return hit
    raise ConfigError(
        f"Unknown category {value!r}. Run with --list-categories to see valid names and IDs."
    )


def resolve_job_type(value: str, who: str) -> str:
    key = value.strip().lower()
    if key in JOB_TYPE_MAP:
        return JOB_TYPE_MAP[key]
    raise ConfigError(
        f"{who}: invalid job_type {value!r}. Valid options: FullTime, PartTime, Contract, Intern, Freelance"
    )


def resolve_workplace(value: str, who: str) -> str:
    key = value.strip().lower()
    if key in WORKPLACE_MAP:
        return WORKPLACE_MAP[key]
    raise ConfigError(f"{who}: invalid workplace {value!r}. Valid options: office, remote, hybrid")


# --------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class ExperienceRule:
    min_years: int = 0
    max_years: int | None = None
    include_unspecified: bool = True


@dataclass(frozen=True)
class ScheduleRule:
    mode: str = "instant"  # "instant" | "digest"
    digest_times: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserProfile:
    name: str
    email: str
    categories: tuple[int, ...] = ()
    experience: ExperienceRule = ExperienceRule()
    keywords: tuple[str, ...] = ()
    required_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    keyword_scope: str = "all"  # "title" | "all"
    locations: tuple[str, ...] = ()
    exclude_locations: tuple[str, ...] = ()
    include_anywhere_in_bd: bool = True
    job_types: tuple[str, ...] = ()  # "FullTime", "PartTime", "Contract", "Intern", "Freelance"
    workplaces: tuple[str, ...] = ()  # "office", "remote", "hybrid"
    min_salary: int | None = None
    include_negotiable_salary: bool = True
    companies: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    max_age_days: int | None = None
    schedule: ScheduleRule = ScheduleRule()
    enabled: bool = True

    def rules_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("name")
        data.pop("email")
        return data


@dataclass(frozen=True)
class Settings:
    poll_interval_minutes: int = 60
    page_size: int = 100
    max_pages_per_category: int = 10
    request_timeout_seconds: float = 20.0
    request_delay_seconds: float = 1.0
    api_urls: tuple[str, ...] = DEFAULT_API_URLS
    job_url_template: str = DEFAULT_JOB_URL_TEMPLATE
    db_path: str = "data/jobs.db"
    instant_batch_threshold: int = 3
    max_jobs_per_email: int = 40
    max_send_attempts: int = 5
    job_retention_days: int = 90
    early_stop_on_known_page: bool = True


@dataclass(frozen=True)
class SmtpSettings:
    user: str
    app_password: str
    host: str = "smtp.gmail.com"
    port: int = 465
    security: str = "ssl"  # "ssl" | "starttls"
    from_name: str = "Job Alert Service"
    timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.user and self.app_password)


@dataclass(frozen=True)
class AppConfig:
    path: Path
    settings: Settings
    users: tuple[UserProfile, ...]
    smtp: SmtpSettings
    mtime: float = 0.0
    env_path: Path | None = None
    env_mtime: float = 0.0

    @property
    def db_path(self) -> Path:
        override = os.environ.get("DB_PATH")
        p = Path(override or self.settings.db_path).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def active_users(self) -> tuple[UserProfile, ...]:
        return tuple(u for u in self.users if u.enabled)

    def rules_hash(self) -> str:
        blob = json.dumps(
            [[u.email.lower(), u.rules_dict()] for u in self.active_users], sort_keys=True
        )
        return hashlib.sha1(blob.encode()).hexdigest()

    def digest_slots(self) -> list[str]:
        slots = set()
        for u in self.active_users:
            if u.schedule.mode == "digest":
                slots.update(u.schedule.digest_times)
        return sorted(slots)


# --------------------------------------------------------------------------- parsing


def _parse_bool(value: Any, field_name: str, who: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    raise ConfigError(f"{who}: '{field_name}' must be true or false")


def _str_list(value: Any, field_name: str, who: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(
        isinstance(v, (str, int)) and not isinstance(v, bool) for v in value
    ):
        raise ConfigError(f"{who}: '{field_name}' must be a list of strings")
    out: list[str] = []
    seen = set()
    for v in value:
        s = str(v).strip()
        low = s.lower()
        if s and low not in seen:
            seen.add(low)
            out.append(s)
    return tuple(out)


def _opt_int(value: Any, field_name: str, who: str, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"{who}: '{field_name}' must be a non-negative integer or null")
    if isinstance(value, int):
        if value < 0:
            raise ConfigError(f"{who}: '{field_name}' must be a non-negative integer or null")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise ConfigError(f"{who}: '{field_name}' must be a non-negative integer or null")
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ConfigError(f"{who}: '{field_name}' must be a non-negative integer or null")


def _parse_user(raw: dict[str, Any], index: int) -> UserProfile:
    if not isinstance(raw, dict):
        raise ConfigError(f"users[{index}] must be an object")
    who = f"users[{index}]"

    name = str(raw.get("name") or "").strip()
    email = str(raw.get("email") or "").strip().lower()
    if not name:
        raise ConfigError(f"{who}: 'name' is required")
    if not EMAIL_RE.match(email):
        raise ConfigError(f"{who} ({name}): invalid email {email!r}")
    who = f"user {name} <{email}>"

    cats_raw = raw.get("categories") or []
    if not isinstance(cats_raw, list):
        raise ConfigError(f"{who}: 'categories' must be a list")
    categories = tuple(dict.fromkeys(resolve_category(c) for c in cats_raw))

    exp = raw.get("experience") or {}
    if not isinstance(exp, dict):
        raise ConfigError(f"{who}: 'experience' must be an object")
    min_y = _opt_int(exp.get("min_years"), "experience.min_years", who, 0) or 0
    max_y = _opt_int(exp.get("max_years"), "experience.max_years", who, None)
    if max_y is not None and max_y < min_y:
        raise ConfigError(f"{who}: experience.max_years < experience.min_years")
    include_unspec = _parse_bool(
        exp.get("include_unspecified"), "experience.include_unspecified", who, True
    )
    experience = ExperienceRule(min_y, max_y, include_unspec)

    locations = _str_list(raw.get("locations"), "locations", who)
    exclude_locations = _str_list(raw.get("exclude_locations"), "exclude_locations", who)
    include_anywhere = _parse_bool(
        raw.get("include_anywhere_in_bd"), "include_anywhere_in_bd", who, True
    )

    raw_jt = _str_list(raw.get("job_types"), "job_types", who)
    job_types = tuple(dict.fromkeys(resolve_job_type(x, who) for x in raw_jt))

    raw_wp = _str_list(raw.get("workplaces"), "workplaces", who)
    workplaces = tuple(dict.fromkeys(resolve_workplace(x, who) for x in raw_wp))

    min_salary = _opt_int(raw.get("min_salary"), "min_salary", who, None)
    if min_salary == 0:
        min_salary = None
    include_negotiable_salary = _parse_bool(
        raw.get("include_negotiable_salary"), "include_negotiable_salary", who, True
    )

    # Company filters & max age
    companies = _str_list(raw.get("companies"), "companies", who)
    exclude_companies = _str_list(raw.get("exclude_companies"), "exclude_companies", who)
    max_age_days = _opt_int(raw.get("max_age_days"), "max_age_days", who, None)
    if max_age_days == 0:
        max_age_days = None

    sched = raw.get("schedule") or {}
    if not isinstance(sched, dict):
        raise ConfigError(f"{who}: 'schedule' must be an object")
    mode = str(sched.get("mode", "instant")).lower().strip()
    if mode not in ("instant", "digest"):
        raise ConfigError(f"{who}: schedule.mode must be 'instant' or 'digest'")
    times = _str_list(
        sched.get("digest_time", sched.get("digest_times")), "schedule.digest_time", who
    )
    for t in times:
        if not TIME_RE.match(t):
            raise ConfigError(f"{who}: digest_time {t!r} must be HH:MM (24h)")
    if mode == "digest" and not times:
        raise ConfigError(f"{who}: schedule.digest_time is required when mode is 'digest'")

    scope = str(raw.get("keyword_scope", "all")).lower().strip()
    if scope not in ("title", "all"):
        raise ConfigError(f"{who}: keyword_scope must be 'title' or 'all'")

    enabled = _parse_bool(raw.get("enabled"), "enabled", who, True)

    return UserProfile(
        name=name,
        email=email,
        categories=categories,
        experience=experience,
        keywords=_str_list(raw.get("keywords"), "keywords", who),
        required_keywords=_str_list(raw.get("required_keywords"), "required_keywords", who),
        exclude_keywords=_str_list(raw.get("exclude_keywords"), "exclude_keywords", who),
        keyword_scope=scope,
        locations=locations,
        exclude_locations=exclude_locations,
        include_anywhere_in_bd=include_anywhere,
        job_types=job_types,
        workplaces=workplaces,
        min_salary=min_salary,
        include_negotiable_salary=include_negotiable_salary,
        companies=companies,
        exclude_companies=exclude_companies,
        max_age_days=max_age_days,
        schedule=ScheduleRule(mode, tuple(sorted(times)) if mode == "digest" else ()),
        enabled=enabled,
    )


def _parse_settings(raw: dict[str, Any]) -> Settings:
    if not isinstance(raw, dict):
        raise ConfigError("'settings' must be an object")
    defaults = Settings()
    known = set(Settings.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        log.warning("Ignoring unknown settings: %s", ", ".join(sorted(unknown)))
    values: dict[str, Any] = {}
    for key in known & set(raw):
        default = getattr(defaults, key)
        val = raw[key]
        if isinstance(default, tuple):
            val = tuple(_str_list(val, key, "settings"))
        elif isinstance(default, bool):
            val = _parse_bool(val, key, "settings", default)
        elif isinstance(default, int):
            parsed = _opt_int(val, key, "settings", None)
            if parsed is None:
                raise ConfigError(f"settings.{key} must be a non-negative integer")
            val = parsed
        elif isinstance(default, float):
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val < 0
            ):
                raise ConfigError(f"settings.{key} must be a non-negative number")
            val = float(val)
        else:
            val = str(val).strip()
        values[key] = val
    env_poll = os.environ.get("POLL_INTERVAL_MINUTES", "").strip()
    if env_poll:
        parsed_poll = _opt_int(env_poll, "POLL_INTERVAL_MINUTES", "env", None)
        if parsed_poll is None:
            raise ConfigError("POLL_INTERVAL_MINUTES must be a non-negative integer")
        values["poll_interval_minutes"] = parsed_poll
    s = Settings(**{**asdict(defaults), **values})
    if s.poll_interval_minutes < 1:
        raise ConfigError("settings.poll_interval_minutes must be >= 1")
    if not 1 <= s.page_size <= 100:
        raise ConfigError("settings.page_size must be between 1 and 100")
    if s.max_pages_per_category < 1:
        raise ConfigError("settings.max_pages_per_category must be >= 1")
    if s.request_timeout_seconds <= 0:
        raise ConfigError("settings.request_timeout_seconds must be > 0")
    if s.max_jobs_per_email < 1:
        raise ConfigError("settings.max_jobs_per_email must be >= 1")
    if s.max_send_attempts < 1:
        raise ConfigError("settings.max_send_attempts must be >= 1")
    if s.job_retention_days < 1:
        raise ConfigError("settings.job_retention_days must be >= 1")
    if not s.api_urls:
        raise ConfigError("settings.api_urls must not be empty")
    if "{job_id}" not in s.job_url_template:
        raise ConfigError("settings.job_url_template must contain '{job_id}'")
    return s


def load_smtp_from_env() -> SmtpSettings:
    security = os.environ.get("SMTP_SECURITY", "").strip().lower()
    raw_port = os.environ.get("SMTP_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else 0
    except ValueError as exc:
        raise ConfigError(f"Invalid SMTP_PORT: {raw_port!r}") from exc
    if not security:
        security = "starttls" if port == 587 else "ssl"
    if security not in ("ssl", "starttls"):
        raise ConfigError("SMTP_SECURITY must be 'ssl' or 'starttls'")
    if not port:
        port = 465 if security == "ssl" else 587
    if not 1 <= port <= 65535:
        raise ConfigError(f"SMTP_PORT must be between 1 and 65535, got {port}")

    raw_timeout = os.environ.get("SMTP_TIMEOUT", "30").strip() or "30"
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ConfigError(f"Invalid SMTP_TIMEOUT: {raw_timeout!r}") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("SMTP_TIMEOUT must be > 0")

    password = re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD", ""))
    if password and len(password) != 16:
        log.warning(
            "GMAIL_APP_PASSWORD is %d characters; Google App Passwords are 16.", len(password)
        )
    return SmtpSettings(
        user=os.environ.get("GMAIL_USER", "").strip(),
        app_password=password,
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com",
        port=port,
        security=security,
        from_name=os.environ.get("MAIL_FROM_NAME", "Job Alert Service").strip()
        or "Job Alert Service",
        timeout_seconds=timeout,
    )


def resolve_env_path(env_path: str | None = None) -> Path:
    return Path(env_path).expanduser() if env_path else PROJECT_ROOT / ".env"


def load_env(env_path: str | None = None, override: bool = False) -> Path:
    """Load ``.env`` into os.environ."""
    path = resolve_env_path(env_path)
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return path
    if path.is_file():
        load_dotenv(path, override=override)
    return path


def default_config_path() -> Path:
    env = os.environ.get("CONFIG_PATH")
    return Path(env).expanduser() if env else PROJECT_ROOT / "config.json"


def _read_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc
    if isinstance(doc, list):
        doc = {"users": doc}
    if not isinstance(doc, dict):
        raise ConfigError(f"{path}: top level must be an object or a list of users")
    return doc


def load_config(
    path: Path | None = None,
    require_users: bool = True,
    env_path: Path | None = None,
) -> AppConfig:
    path = Path(path) if path else default_config_path()
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path} (copy config.example.json to config.json)"
        )
    doc = _read_document(path)
    settings = _parse_settings(doc.get("settings") or {})
    users_raw = doc.get("users") or []
    if not isinstance(users_raw, list):
        raise ConfigError("'users' must be a list")
    users = tuple(_parse_user(u, i) for i, u in enumerate(users_raw))
    emails = [u.email for u in users]
    dupes = {e for e in emails if emails.count(e) > 1}
    if dupes:
        raise ConfigError(f"Duplicate user emails: {', '.join(sorted(dupes))}")
    if require_users and not any(u.enabled for u in users):
        raise ConfigError("No enabled users configured")
    ep = Path(env_path) if env_path else PROJECT_ROOT / ".env"
    env_mtime = ep.stat().st_mtime if ep.is_file() else 0.0
    return AppConfig(
        path=path,
        settings=settings,
        users=users,
        smtp=load_smtp_from_env(),
        mtime=path.stat().st_mtime,
        env_path=ep,
        env_mtime=env_mtime,
    )
