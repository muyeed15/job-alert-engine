"""Job and user profile matching: categories, locations, keywords, experience, salary, and work mode."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from re import Pattern

from .config import UserProfile
from .scraper import Job

_NUM = r"(\d+(?:\.\d+)?)"
_UNSPECIFIED = (
    "na",
    "n/a",
    "n a",
    "none",
    "not required",
    "not applicable",
    "no experience",
    "no experience required",
    "negotiable",
    "any",
    "not mentioned",
    "",
)
_FRESHER = ("fresher", "freshers", "fresh graduate", "entry level")
_DASH_TABLE = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)

# Regional location expansions so selecting "Dhaka" or "Chattogram" also matches listings
# where the employer specified only a neighborhood/thana name (e.g. "Uttara", "Banani", "Agrabad").
LOCATION_GROUPS: dict[str, tuple[str, ...]] = {
    "dhaka": (
        "dhaka",
        "uttara",
        "gulshan",
        "banani",
        "baridhara",
        "bashundhara",
        "basundhara",
        "badda",
        "rampura",
        "banasree",
        "aftabnagar",
        "khilgaon",
        "malibagh",
        "malibag",
        "moghbazar",
        "eskaton",
        "banglamotor",
        "farmgate",
        "karwan bazar",
        "kawran bazar",
        "panthapath",
        "tejgaon",
        "mohakhali",
        "niketan",
        "dhanmondi",
        "kalabagan",
        "new market",
        "shahbag",
        "shahbagh",
        "ramna",
        "motijheel",
        "dilkhusha",
        "paltan",
        "naya paltan",
        "purana paltan",
        "kakrail",
        "segunbagicha",
        "shantinagar",
        "siddheswari",
        "wari",
        "jatrabari",
        "sutrapur",
        "kotwali",
        "lalbagh",
        "hazaribagh",
        "kamrangirchar",
        "shyampur",
        "demra",
        "kadamtali",
        "sabujbagh",
        "mugda",
        "mirpur",
        "pallabi",
        "kafrul",
        "shewrapara",
        "kazipara",
        "agargaon",
        "sher-e-bangla nagar",
        "shyamoli",
        "kalyanpur",
        "gabtoli",
        "darussalam",
        "mohammadpur",
        "lalmatia",
        "adabor",
        "cantonment",
        "bhashantek",
        "khilkhet",
        "nikunja",
        "kuril",
        "airport",
        "dakshinkhan",
        "uttarkhan",
        "turag",
        "vatara",
        "bhatara",
        "satarkul",
        "savar",
        "ashulia",
        "depz",
        "dhamrai",
        "keraniganj",
        "dohar",
        "nawabganj",
    ),
    "chattogram": (
        "chattogram",
        "chittagong",
        "agrabad",
        "nasirabad",
        "halishahar",
        "panchlaish",
        "khulshi",
        "gec",
        "pahartali",
        "patenga",
        "cepz",
        "kepz",
        "chawkbazar",
        "kalurghat",
        "baizid",
        "bayazid",
        "bakalia",
        "bandar",
        "double mooring",
        "chandgaon",
        "hathazari",
        "sitakunda",
        "mirsharai",
        "anwara",
        "patiya",
        "boalkhali",
        "raozan",
        "rangunia",
        "karnaphuli",
    ),
    "chittagong": (
        "chattogram",
        "chittagong",
        "agrabad",
        "nasirabad",
        "halishahar",
        "panchlaish",
        "khulshi",
        "gec",
        "pahartali",
        "patenga",
        "cepz",
        "kepz",
        "chawkbazar",
        "kalurghat",
        "baizid",
        "bayazid",
        "bakalia",
        "bandar",
        "double mooring",
        "chandgaon",
        "hathazari",
        "sitakunda",
        "mirsharai",
        "anwara",
        "patiya",
        "boalkhali",
        "raozan",
        "rangunia",
        "karnaphuli",
    ),
    "cumilla": ("cumilla", "comilla"),
    "comilla": ("cumilla", "comilla"),
    "jashore": ("jashore", "jessore"),
    "jessore": ("jashore", "jessore"),
    "barishal": ("barishal", "barisal"),
    "barisal": ("barishal", "barisal"),
    "bogura": ("bogura", "bogra"),
    "bogra": ("bogura", "bogra"),
    "gazipur": ("gazipur", "tongi", "kaliakair", "sreepur", "bhaluka"),
    "narayanganj": ("narayanganj", "rupganj", "sonargaon", "siddhirganj", "fatullah", "araihazar"),
}


@dataclass(frozen=True)
class ExperienceRange:
    min_years: float
    max_years: float | None  # None means open-ended
    specified: bool  # False for "NA", "Negotiable", "Not Required", or unparseable

    def describe(self) -> str:
        if not self.specified:
            return "Not specified"
        if self.max_years is None:
            return f"{self.min_years:g}+ years"
        if self.min_years == self.max_years:
            return f"{self.min_years:g} years"
        return f"{self.min_years:g}-{self.max_years:g} years"


UNSPECIFIED = ExperienceRange(0, None, False)


def parse_experience(text: str | None) -> ExperienceRange:
    """Parse experience text into an ExperienceRange."""
    raw = (text or "").translate(_DASH_TABLE).strip().lower()
    s = re.sub(r"\(s\)", "s", raw)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if s in _UNSPECIFIED or "not required" in s or "negotiable" in s:
        return UNSPECIFIED
    has_fresher = any(f in s for f in _FRESHER)
    if has_fresher and not re.search(r"\d", s):
        return ExperienceRange(0, 0, True)

    scale = 1 / 12 if re.search(r"\bmonths?\b", s) and not re.search(r"\byears?\b", s) else 1.0

    def f(v: str) -> float:
        return round(float(v) * scale, 2)

    m = re.search(rf"{_NUM}\s*(?:to|-|~)\s*{_NUM}", s)
    if m:
        lo, hi = sorted((f(m.group(1)), f(m.group(2))))
        if has_fresher:
            lo = 0.0
        return ExperienceRange(lo, hi, True)
    m = re.search(
        rf"(?:at\s*most|up\s*to|maximum|max\.?|not more than|less than|below)\s*{_NUM}", s
    )
    if m:
        return ExperienceRange(0, f(m.group(1)), True)
    m = re.search(rf"(?:at\s*least|minimum|min\.?|more than|over|above)\s*{_NUM}", s)
    if m:
        lo = 0.0 if has_fresher else f(m.group(1))
        return ExperienceRange(lo, None, True)
    m = re.search(rf"{_NUM}\s*\+", s)
    if m:
        lo = 0.0 if has_fresher else f(m.group(1))
        return ExperienceRange(lo, None, True)
    m = re.search(rf"{_NUM}\s*(?:years?|yrs?|months?)", s) or re.fullmatch(_NUM, s)
    if m:
        v = f(m.group(1))
        return ExperienceRange(0.0 if has_fresher else v, v, True)
    return UNSPECIFIED


def experience_matches(job_range: ExperienceRange, user: UserProfile) -> bool:
    rule = user.experience
    if not job_range.specified:
        return rule.include_unspecified
    if rule.max_years is not None and job_range.min_years > rule.max_years:
        return False
    if job_range.max_years is not None and job_range.max_years < rule.min_years:
        return False
    return True


def parse_salary_max(salary_text: str | None) -> int | None:
    """Return the upper monthly salary figure in BDT if numeric, or None if negotiable/undisclosed."""
    if not salary_text:
        return None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", salary_text)]
    for k_val in re.findall(r"\b(\d+(?:\.\d+)?)\s*k\b", salary_text, re.IGNORECASE):
        nums.append(int(float(k_val) * 1000))
    valid = [n for n in nums if n >= 500]
    return max(valid) if valid else None


def salary_matches(job_salary: str, user: UserProfile) -> bool:
    if user.min_salary is None:
        return True
    hi = parse_salary_max(job_salary)
    if hi is None:
        return user.include_negotiable_salary
    return hi >= user.min_salary


def _expand_locations(terms: Sequence[str]) -> tuple[str, ...]:
    expanded: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.strip().lower()
        if not key:
            continue
        group = LOCATION_GROUPS.get(key, (key,))
        for item in group:
            if item not in seen:
                seen.add(item)
                expanded.append(item)
    return tuple(expanded)


def _compile(words: Sequence[str]) -> Pattern[str] | None:
    if not words:
        return None
    parts = []
    for w in sorted(words, key=len, reverse=True):
        tokens = w.strip().split()
        if tokens:
            body = r"\s+".join(re.escape(t) for t in tokens)
            prefix = r"(?<![a-z0-9])" if tokens[0][0].isalnum() else ""
            suffix = r"(?![a-z0-9])" if tokens[-1][-1].isalnum() else ""
            parts.append(f"{prefix}{body}{suffix}")
    if not parts:
        return None
    alts = "|".join(parts)
    return re.compile(rf"(?:{alts})", re.IGNORECASE)


def _norm_kw(text: str) -> str:
    return " ".join(text.lower().split())


def _job_workplace_modes(job: Job) -> set[str]:
    modes: set[str] = set()
    wp = (job.workplace or "").strip().lower()
    search_low = job.search_text.lower()
    if (
        "home" in wp
        or "remote" in wp
        or re.search(r"\b(?:remote|work from home|wfh)\b", search_low)
    ):
        modes.add("remote")
    if "hybrid" in wp or re.search(r"\bhybrid\b", search_low):
        modes.add("hybrid")
    if "office" in wp or (not wp and "remote" not in modes):
        modes.add("office")
    return modes


@dataclass(frozen=True)
class MatchResult:
    job: Job
    matched_keywords: tuple[str, ...]
    experience: ExperienceRange


class UserMatcher:
    def __init__(self, user: UserProfile):
        self.user = user
        self.categories = frozenset(user.categories)
        self._include = _compile(user.keywords)
        self._required = [_compile([rk]) for rk in user.required_keywords if rk.strip()]
        self._exclude = _compile(user.exclude_keywords)
        self._canonical: dict[str, str] = {
            _norm_kw(k): k for k in (*user.keywords, *user.required_keywords)
        }
        self._loc_include = _compile(_expand_locations(user.locations))
        self._loc_exclude = _compile(_expand_locations(user.exclude_locations))
        self._comp_include = _compile(user.companies)
        self._comp_exclude = _compile(user.exclude_companies)
        self._job_types = frozenset(jt.lower() for jt in user.job_types)
        self._workplaces = frozenset(wp.lower() for wp in user.workplaces)

    def _location_matches(self, job: Job) -> bool:
        loc = (job.location or "").strip()
        if self._loc_exclude and loc and self._loc_exclude.search(loc):
            return False
        if not self._loc_include:
            return True
        if not loc:
            return self.user.include_anywhere_in_bd
        if self.user.include_anywhere_in_bd and "anywhere in bangladesh" in loc.lower():
            return True
        return bool(self._loc_include.search(loc))

    def _job_type_matches(self, job: Job) -> bool:
        if not self._job_types:
            return True
        jt = (job.job_type or "").lower()
        for wanted in self._job_types:
            if wanted in jt:
                return True
            if wanted == "intern" and re.search(r"\bintern(?:ship)?\b", job.title.lower()):
                return True
        return False

    def _workplace_matches(self, job: Job) -> bool:
        if not self._workplaces:
            return True
        return bool(self._workplaces & _job_workplace_modes(job))

    def _company_matches(self, job: Job) -> bool:
        comp = job.company or ""
        if self._comp_exclude and comp and self._comp_exclude.search(comp):
            return False
        if self._comp_include:
            return bool(comp and self._comp_include.search(comp))
        return True

    def _age_matches(self, job: Job) -> bool:
        if self.user.max_age_days is None or not job.posted_at:
            return True
        try:
            dt = datetime.fromisoformat(str(job.posted_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        except ValueError:
            return True
        cutoff = datetime.now(UTC) - timedelta(days=self.user.max_age_days)
        return dt >= cutoff

    def match(self, job: Job, exp: ExperienceRange | None = None) -> MatchResult | None:
        if self.categories and not (self.categories & job.category_ids):
            return None
        if not self._location_matches(job):
            return None
        if not self._job_type_matches(job):
            return None
        if not self._workplace_matches(job):
            return None
        if not self._company_matches(job):
            return None
        if not salary_matches(job.salary, self.user):
            return None
        if not self._age_matches(job):
            return None
        exp = exp or parse_experience(job.experience)
        if not experience_matches(exp, self.user):
            return None
        haystack = job.title if self.user.keyword_scope == "title" else job.search_text
        if self._exclude and self._exclude.search(job.title):
            return None

        found: set[str] = set()
        for req_pat in self._required:
            if req_pat is None:
                continue
            m = req_pat.search(haystack)
            if not m:
                return None
            found.add(self._canonical.get(_norm_kw(m.group(0)), m.group(0)))

        if self._include:
            inc_hits = {
                self._canonical.get(_norm_kw(m.group(0)), m.group(0))
                for m in self._include.finditer(haystack)
            }
            if not inc_hits:
                return None
            found.update(inc_hits)

        hits = tuple(sorted(found, key=str.lower))
        return MatchResult(job, hits, exp)


class Matcher:
    """Matches a batch of jobs against all active users."""

    def __init__(self, users: Sequence[UserProfile]):
        self.users = [UserMatcher(u) for u in users if u.enabled]

    def match_all(self, jobs: Sequence[Job]) -> dict[str, list[MatchResult]]:
        results: dict[str, list[MatchResult]] = {m.user.email: [] for m in self.users}
        for job in jobs:
            exp = parse_experience(job.experience)
            for m in self.users:
                r = m.match(job, exp)
                if r:
                    results[m.user.email].append(r)
        return results
