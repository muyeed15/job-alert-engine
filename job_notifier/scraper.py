"""Job listing ingestion via JSON search API endpoints."""

from __future__ import annotations

import hashlib
import html
import logging
import random
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .config import Settings

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Translate common Unicode punctuation to plain ASCII equivalents.
_UNICODE_TO_ASCII = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2026": "...",
        "\u2022": "-",
        "\u2023": "-",
        "\u25e6": "-",
        "\u2043": "-",
        "\u00b7": "-",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2007": " ",
    }
)


def strip_html(text: str | None, limit: int = 2000) -> str:
    """Strip HTML tags, decode entities, and normalize text to clean printable ASCII."""
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", str(text))
    for _ in range(2):
        unescaped = html.unescape(cleaned)
        if unescaped == cleaned:
            break
        cleaned = unescaped
    cleaned = cleaned.translate(_UNICODE_TO_ASCII)
    ascii_chars = [ch if 32 <= ord(ch) <= 126 else " " for ch in cleaned]
    return _WS_RE.sub(" ", "".join(ascii_chars)).strip()[:limit]


def _norm_key(text: str) -> str:
    return _WS_RE.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    url: str
    location: str = ""
    experience: str = ""
    deadline: str = ""
    deadline_iso: str | None = None
    posted_at: str | None = None
    salary: str = ""
    job_type: str = ""
    workplace: str = ""
    category_ids: set[int] = field(default_factory=set)
    description: str = ""

    @property
    def fingerprint(self) -> str:
        """Content hash used to detect re-posts of the same vacancy under a new job_id."""
        basis = "|".join(
            (
                _norm_key(self.title),
                _norm_key(self.company),
                (self.deadline_iso or self.deadline)[:10],
            )
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    @property
    def search_text(self) -> str:
        return " ".join((self.title, self.company, self.description))


def _get(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    """Case-tolerant getter so minor API field renames do not break ingestion."""
    for k in keys:
        if k in item and item[k] not in (None, ""):
            return item[k]
    lowered = {k.lower(): v for k, v in item.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v not in (None, ""):
            return v
    return default


def parse_job(
    item: dict[str, Any], url_template: str, category_id: int | None = None
) -> Job | None:
    if not isinstance(item, dict):
        return None
    try:
        job_id = strip_html(str(_get(item, "Jobid", "JobId", "jobId", "id")), 64)
        title = strip_html(str(_get(item, "jobTitle", "JobTitle", "title")), 300)
        if not job_id or not title:
            return None
        cats: set[int] = set()
        raw_cat = _get(item, "Cat_id", "CatId", "categoryId", default=None)
        try:
            if raw_cat is not None and int(raw_cat) > 0:
                cats.add(int(raw_cat))
        except (TypeError, ValueError):
            pass
        if category_id is not None:
            cats.add(category_id)
        salary = strip_html(str(_get(item, "Salary", "salary")), 120)
        description = " ".join(
            strip_html(_get(item, k), 1000) for k in ("jobContext", "jobDescription", "eduRec")
        )
        deadline_iso_raw = _get(item, "deadlineDB", "DeadlineDB", default=None)
        posted_at_raw = _get(item, "publishDate", "PublishDate", "postedOn", default=None)
        try:
            url = url_template.format(job_id=job_id)
        except (KeyError, IndexError, ValueError):
            url = f"https://bdjobs.com/h/details/{job_id}"
        return Job(
            job_id=job_id,
            title=title,
            company=strip_html(str(_get(item, "companyName", "CompanyName", "company")), 300),
            url=url,
            location=strip_html(str(_get(item, "location", "Location")), 300),
            experience=strip_html(str(_get(item, "experience", "Experience")), 120),
            deadline=strip_html(str(_get(item, "deadline", "Deadline")), 60),
            deadline_iso=strip_html(str(deadline_iso_raw), 40) if deadline_iso_raw else None,
            posted_at=strip_html(str(posted_at_raw), 40) if posted_at_raw else None,
            salary="" if salary in ("--", "-", "0") else salary,
            job_type=strip_html(str(_get(item, "JobType", "jobType")), 60),
            workplace=strip_html(str(_get(item, "WorkPlace", "workPlace")), 60),
            category_ids=cats,
            description=_WS_RE.sub(" ", description).strip()[:2000],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Skipping malformed job item: %s", exc)
        return None


class ScrapeError(RuntimeError):
    pass


class JobScraper:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or self._build_session(settings)
        self._api_index = 0
        self.last_partial_failure = False

    @staticmethod
    def _build_session(settings: Settings | None = None) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=4,
            connect=4,
            read=3,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        headers = {
            "User-Agent": f"Mozilla/5.0 (X11; Linux x86_64) job-alert-engine/{__version__}",
            "Accept": "application/json, text/plain, */*",
        }
        if settings and settings.job_url_template:
            parts = urlsplit(settings.job_url_template)
            if parts.scheme and parts.netloc:
                origin = f"{parts.scheme}://{parts.netloc}"
                headers["Origin"] = origin
                headers["Referer"] = f"{origin}/"
        s.headers.update(headers)
        return s

    def close(self) -> None:
        self.session.close()

    # ------------------------------------------------------------------ HTTP

    def _fetch_page(self, category_id: int | None, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {"rpp": self.settings.page_size, "pg": page}
        if category_id is not None:
            params["category"] = category_id
        urls = self.settings.api_urls
        errors: list[str] = []
        for offset in range(len(urls)):
            idx = (self._api_index + offset) % len(urls)
            url = urls[idx]
            resp = None
            try:
                resp = self.session.get(
                    url, params=params, timeout=self.settings.request_timeout_seconds
                )
                resp.raise_for_status()
                if len(resp.content) > 5 * 1024 * 1024:
                    raise ScrapeError("response payload exceeds 5MB limit")
                payload = resp.json()
                if not isinstance(payload, dict) or "data" not in payload:
                    raise ScrapeError("unexpected payload structure")
                if payload["data"] is not None and not isinstance(payload["data"], list):
                    raise ScrapeError("unexpected payload data type")
                self._api_index = idx
                return payload
            except (
                requests.RequestException,
                ValueError,
                KeyError,
                TypeError,
                OSError,
                ScrapeError,
            ) as exc:
                errors.append(f"{url}: {exc}")
                log.warning("API request failed (%s): %s", url, exc)
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:  # noqa: BLE001
                        pass
        raise ScrapeError("; ".join(errors))

    def _fetch_page_with_backoff(
        self, category_id: int | None, page: int, attempts: int = 3
    ) -> dict[str, Any]:
        delay = 5.0
        for attempt in range(1, attempts + 1):
            try:
                return self._fetch_page(category_id, page)
            except ScrapeError:
                if attempt == attempts:
                    raise
                sleep_for = delay + random.uniform(0, delay / 2)
                log.info("Retrying category=%s page=%s in %.0fs", category_id, page, sleep_for)
                time.sleep(sleep_for)
                delay *= 3
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ public

    def iter_category(
        self,
        category_id: int | None,
        is_known: Callable[[Iterable[str]], bool] | None = None,
    ) -> Iterator[Job]:
        """Yield jobs for one category (None = all categories), newest first."""
        max_pages = max(1, self.settings.max_pages_per_category)
        page = 1
        while page <= max_pages:
            payload = self._fetch_page_with_backoff(category_id, page)
            raw_data = payload.get("data")
            items = list(raw_data) if isinstance(raw_data, list) else []
            if page == 1:
                raw_premium = payload.get("premiumData")
                if isinstance(raw_premium, list):
                    items.extend(raw_premium)
            jobs = [
                j
                for j in (parse_job(i, self.settings.job_url_template, category_id) for i in items)
                if j
            ]
            yield from jobs

            try:
                total_pages = int((payload.get("common") or {}).get("totalpages") or 0)
            except (TypeError, ValueError):
                total_pages = 0
            if not items or (total_pages and page >= total_pages):
                break
            if (
                is_known is not None
                and self.settings.early_stop_on_known_page
                and jobs
                and is_known([j.job_id for j in jobs])
            ):
                log.debug("category=%s: page %d fully known, stopping early", category_id, page)
                break
            page += 1
            if page <= max_pages and self.settings.request_delay_seconds > 0:
                time.sleep(self.settings.request_delay_seconds)

    def fetch(
        self,
        category_ids: Iterable[int | None],
        is_known: Callable[[Iterable[str]], bool] | None = None,
    ) -> dict[str, Job]:
        """Fetch all requested categories and merge duplicates."""
        jobs: dict[str, Job] = {}
        failures = 0
        cats = list(category_ids)
        for idx, cat in enumerate(cats):
            label = "ALL" if cat is None else f"{cat}"
            count = 0
            try:
                for job in self.iter_category(cat, is_known):
                    count += 1
                    existing = jobs.get(job.job_id)
                    if existing:
                        existing.category_ids |= job.category_ids
                    else:
                        jobs[job.job_id] = job
            except ScrapeError as exc:
                failures += 1
                log.error("Category %s fetch failed: %s", label, exc)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log.exception("Unexpected error fetching category %s: %s", label, exc)
            log.info("Category %s: %d listings", label, count)
            if idx + 1 < len(cats) and self.settings.request_delay_seconds > 0:
                time.sleep(self.settings.request_delay_seconds)
        self.last_partial_failure = failures > 0
        if cats and failures == len(cats):
            raise ScrapeError("All category fetches failed")
        return jobs
