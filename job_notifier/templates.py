"""Email templates: Classic OS gray and white theme with 3D red button + plaintext fallback."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from .config import CATEGORIES
from .scraper import strip_html

_FONT = "Tahoma,Arial,sans-serif"


@dataclass(frozen=True)
class AlertItem:
    title: str
    company: str
    url: str
    experience: str = ""
    location: str = ""
    deadline: str = ""
    salary: str = ""
    job_type: str = ""
    workplace: str = ""
    posted: str = ""
    categories: str = ""
    matched: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AlertItem:
        posted = ""
        if row["posted_at"]:
            try:
                dt = datetime.fromisoformat(str(row["posted_at"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                posted = dt.astimezone().strftime("%d %b %Y, %I:%M %p")
            except ValueError:
                posted = str(row["posted_at"])
        cats = {
            int(x)
            for x in (row["category_ids"] or "").split(",")
            if x.strip().lstrip("-").isdigit()
        }
        keys = set(row.keys())
        return cls(
            title=strip_html(row["title"], 300),
            company=strip_html(row["company"] or "", 300),
            url=strip_html(row["url"], 500),
            experience=strip_html(row["experience"] or "", 120),
            location=strip_html(row["location"] or "", 300),
            deadline=strip_html(row["deadline"] or "", 60),
            salary=strip_html(row["salary"] or "", 120),
            job_type=strip_html((row["job_type"] if "job_type" in keys else "") or "", 60),
            workplace=strip_html((row["workplace"] if "workplace" in keys else "") or "", 60),
            posted=strip_html(posted, 60),
            categories=_cat_names(cats),
            matched=tuple(
                strip_html(x, 80) for x in (row["matched"] or "").split(",") if x.strip()
            ),
        )


def _cat_names(ids) -> str:
    return ", ".join(CATEGORIES.get(i, f"#{i}") for i in sorted(ids))


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


# ----------------------------------------------------------------------------- HTML parts


def _fields(item: AlertItem) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [
        ("Company", item.company or "N/A"),
        ("Location", item.location or "N/A"),
        ("Experience", item.experience or "Not specified"),
        ("Deadline", item.deadline or "N/A"),
    ]
    if item.salary:
        rows.append(("Salary", item.salary))
    if item.job_type or item.workplace:
        jt_parts = [p for p in (item.job_type, item.workplace) if p]
        rows.append(("Job Type", " / ".join(jt_parts)))
    if item.categories:
        rows.append(("Category", item.categories))
    if item.posted:
        rows.append(("Posted", item.posted))
    if item.matched:
        rows.append(("Matched", ", ".join(item.matched)))
    return rows


def _panel_html(item: AlertItem, index: int = 0) -> str:
    prefix = f"{index}. " if index else ""
    url = escape(item.url, quote=True)
    field_rows = _fields(item)
    prop_rows: list[str] = []
    for label, val in field_rows:
        prop_rows.append(
            f"<tr>"
            f'<td width="110" valign="top" bgcolor="#EAEAEA" style="width:110px;background-color:#EAEAEA;'
            f"border-bottom:1px solid #CCCCCC;border-right:1px solid #808080;padding:4px 8px;"
            f'font-family:{_FONT};font-size:11px;font-weight:bold;color:#000000;white-space:nowrap;">'
            f"{escape(label)}</td>"
            f'<td valign="top" bgcolor="#FFFFFF" style="background-color:#FFFFFF;'
            f"border-bottom:1px solid #CCCCCC;padding:4px 8px;"
            f'font-family:{_FONT};font-size:11px;color:#000000;">'
            f"{escape(val)}</td>"
            f"</tr>"
        )
    prop_rows_str = "".join(prop_rows)

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'bgcolor="#FFFFFF" style="width:100%;max-width:600px;margin:0 auto 16px auto;background-color:#FFFFFF;'
        'border:1px solid #808080;border-collapse:collapse;">'
        "<tr>"
        '<td colspan="2" bgcolor="#DCDCDC" style="background-color:#DCDCDC;'
        "background-image:linear-gradient(to bottom,#EEEEEE 0%,#D0D0D0 100%);"
        'border-bottom:1px solid #808080;padding:6px 10px;">'
        f'<a href="{url}" target="_blank" style="font-family:{_FONT};font-size:12px;font-weight:bold;'
        f'color:#000000;text-decoration:none;">{escape(prefix + item.title)}</a>'
        "</td>"
        "</tr>"
        f"{prop_rows_str}"
        "<tr>"
        '<td colspan="2" bgcolor="#D8D8D8" style="background-color:#D8D8D8;'
        'border-top:1px solid #808080;padding:6px 10px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="right" style="margin:0;border-collapse:collapse;">'
        "<tr>"
        '<td align="center" valign="middle" bgcolor="#B22222" style="background-color:#B22222;'
        "background-image:linear-gradient(to bottom,#D32F2F 0%,#8B0000 100%);"
        'border-top:1px solid #FF8080;border-left:1px solid #FF8080;border-right:1px solid #500000;border-bottom:1px solid #500000;padding:0;">'
        f'<a href="{url}" target="_blank" style="display:inline-block;padding:4px 14px;'
        f"font-family:{_FONT};font-size:11px;font-weight:bold;color:#FFFFFF;text-decoration:none;"
        'white-space:nowrap;line-height:16px;">View Job Listing</a>'
        "</td>"
        "</tr>"
        "</table>"
        "</td>"
        "</tr>"
        "</table>"
    )


def _layout(title: str, body: str, banner: str = "") -> str:
    clean_title = strip_html(title, 200)
    banner_html = ""
    if banner:
        banner_html = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'bgcolor="#DCDCDC" style="width:100%;max-width:600px;margin:0 auto 12px auto;background-color:#DCDCDC;'
            'border:1px solid #808080;border-collapse:collapse;">'
            "<tr>"
            f'<td style="padding:6px 10px;font-family:{_FONT};font-size:12px;font-weight:bold;color:#000000;">'
            f"{escape(banner)}</td>"
            "</tr></table>"
        )

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        f"<title>{escape(clean_title)}</title>"
        "<style>:root{color-scheme:light;}body{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;margin:0;padding:0;}</style>"
        "</head>"
        f'<body bgcolor="#D8D8D8" style="margin:0;padding:16px 10px;background-color:#D8D8D8;'
        f'color:#000000;font-family:{_FONT};font-size:12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'bgcolor="#D8D8D8" style="background-color:#D8D8D8;margin:0;padding:0;">'
        "<tr>"
        '<td align="center" valign="top" bgcolor="#D8D8D8" style="background-color:#D8D8D8;padding:0;">'
        '<div style="width:100%;max-width:600px;margin:0 auto;text-align:left;">'
        f"{banner_html}"
        f"{body}"
        "</div>"
        "</td>"
        "</tr>"
        "</table>"
        "</body></html>"
    )


def _panel_text(item: AlertItem, index: int = 0) -> str:
    prefix = f"{index}. " if index else ""
    lines = [
        f"{prefix}{item.title}",
        "-" * 68,
    ]
    for k, v in _fields(item):
        lines.append(f"{k:<12}: {v}")
    lines.append(f"{'Link':<12}: {item.url}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- builders


def build_instant_email(item: AlertItem) -> EmailContent:
    company = item.company or "New Listing"
    subject = strip_html(f"Job Alert: {item.title} - {company}", 200)
    html = _layout(subject, _panel_html(item))
    text = _panel_text(item) + "\n"
    return EmailContent(subject, text, html)


def build_digest_email(items: Sequence[AlertItem], label: str = "digest") -> EmailContent:
    n = len(items)
    plural = "listing" if n == 1 else "listings"
    today = datetime.now().strftime("%d %b %Y")
    if label == "digest":
        subject = strip_html(f"Job Digest: {n} new {plural} ({today})", 200)
        banner = f"Job Digest: {n} new {plural} ({today})"
    else:
        subject = strip_html(f"Job Alert: {n} new matching {plural} ({today})", 200)
        banner = f"Job Alert: {n} new matching {plural} ({today})"
    body_html = "".join(_panel_html(i, idx) for idx, i in enumerate(items, 1))
    body_text = ("\n\n" + "=" * 68 + "\n\n").join(
        _panel_text(i, idx) for idx, i in enumerate(items, 1)
    ) + "\n"
    return EmailContent(subject, body_text, _layout(subject, body_html, banner=banner))


def sample_items() -> Sequence[AlertItem]:
    return (
        AlertItem(
            title="Junior Python Backend Developer",
            company="Example Systems Ltd.",
            url="https://jobs.example.com/details/100001",
            experience="At most 1 year",
            location="Dhaka",
            deadline="Dec 31, 2026",
            salary="Tk. 30000 - 40000 (Monthly)",
            job_type="FullTime",
            workplace="Office",
            posted=datetime.now().strftime("%d %b %Y, %I:%M %p"),
            categories="IT/Telecommunication",
            matched=("Python", "Backend"),
        ),
        AlertItem(
            title="Software Engineer (Backend Services)",
            company="Standard Enterprise Corp.",
            url="https://jobs.example.com/details/100002",
            experience="1 to 3 years",
            location="Dhaka",
            deadline="Dec 15, 2026",
            job_type="FullTime",
            workplace="Office",
            categories="IT/Telecommunication",
            matched=("Software Engineer", "Backend"),
        ),
    )
