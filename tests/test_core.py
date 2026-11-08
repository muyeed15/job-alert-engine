"""Offline unit tests: experience parsing, text cleaning, matching, deduplication, and templates."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_notifier.config import (
    AppConfig,
    ConfigError,
    ExperienceRule,
    ScheduleRule,
    Settings,
    SmtpSettings,
    UserProfile,
    _parse_bool,
    _parse_settings,
    resolve_category,
)
from job_notifier.db import Database
from job_notifier.engine import AlertEngine
from job_notifier.manage import main as manage_main
from job_notifier.matcher import UserMatcher, parse_experience
from job_notifier.scraper import Job, ScrapeError, parse_job, strip_html
from job_notifier.templates import build_digest_email, build_instant_email, sample_items


def user(**kw) -> UserProfile:
    base = dict(
        name="Test User",
        email="t@example.com",
        categories=(8,),
        experience=ExperienceRule(0, 1),
        keywords=("Python", "Backend", "Software Engineer"),
        exclude_keywords=(),
        keyword_scope="all",
        schedule=ScheduleRule("instant"),
        enabled=True,
    )
    base.update(kw)
    return UserProfile(**base)


def job(
    job_id="1",
    title="Python Developer",
    exp="1 to 3 years",
    cats=(8,),
    deadline="2026-12-01",
) -> Job:
    return Job(
        job_id=job_id,
        title=title,
        company="ACME Corp",
        url=f"https://jobs.example.com/details/{job_id}",
        experience=exp,
        deadline_iso=deadline,
        category_ids=set(cats),
    )


class ExperienceParsing(unittest.TestCase):
    def test_patterns(self):
        cases = {
            "At most 1 year": (0, 1, True),
            "1 to 3 year(s)": (1, 3, True),
            "1 to 3 years": (1, 3, True),
            "1 - 3 years (Freshers encouraged)": (0, 3, True),
            "At least 5 years": (5, None, True),
            "Minimum 2 years": (2, None, True),
            "3+ years": (3, None, True),
            "2 years": (2, 2, True),
            "6 months": (0.5, 0.5, True),
            "Fresher": (0, 0, True),
            "NA": (0, None, False),
            "Negotiable": (0, None, False),
            "Not Required": (0, None, False),
            "": (0, None, False),
        }
        for text, (lo, hi, spec) in cases.items():
            r = parse_experience(text)
            self.assertEqual((r.min_years, r.max_years, r.specified), (lo, hi, spec), text)


class TextSanitization(unittest.TestCase):
    def test_strip_html_and_unicode(self):
        raw = "<p>Sr. Engineer \u2013 Backend \u2014 Team &amp;amp; Co\u2026 \U0001f680</p>"
        cleaned = strip_html(raw)
        self.assertEqual(cleaned, "Sr. Engineer - Backend - Team & Co...")
        self.assertTrue(all(32 <= ord(c) <= 126 for c in cleaned))

    def test_templates_pure_ascii_and_javafx_style(self):
        items = sample_items()
        instant = build_instant_email(items[0])
        digest = build_digest_email(items)
        for content in (instant, digest):
            self.assertTrue(all(ord(c) < 128 for c in content.subject))
            self.assertTrue(all(ord(c) < 128 for c in content.text))
            self.assertTrue(all(ord(c) < 128 for c in content.html))
            self.assertIn('bgcolor="#D8D8D8"', content.html)
            self.assertIn('bgcolor="#FFFFFF"', content.html)
            self.assertIn('bgcolor="#B22222"', content.html)
            self.assertIn("View Job Listing", content.html)
            self.assertIn("border-top:1px solid #FF8080", content.html)
            self.assertNotIn("Status:", content.html)
            self.assertNotIn("config.json", content.html)
            self.assertNotIn("Ref ID", content.html)
            self.assertNotIn("Ready", content.html)
            self.assertNotIn("border-radius", content.html)
            self.assertNotIn("border-top:3px", content.html)
            self.assertNotIn("color:#8B0000", content.html)


class Matching(unittest.TestCase):
    def test_entry_level(self):
        m = UserMatcher(user())
        self.assertIsNotNone(m.match(job(exp="At most 1 year")))
        self.assertIsNotNone(m.match(job(exp="1 to 3 years")))
        self.assertIsNone(m.match(job(exp="At least 3 years")))
        self.assertIsNotNone(m.match(job(exp="Not Required")))

    def test_unspecified_excluded(self):
        m = UserMatcher(user(experience=ExperienceRule(2, None, include_unspecified=False)))
        self.assertIsNone(m.match(job(exp="NA")))
        self.assertIsNotNone(m.match(job(exp="At least 5 years")))

    def test_keywords_case_boundaries_and_whitespace(self):
        m = UserMatcher(
            user(
                keywords=("python", "C++", "IT", "Software Engineer"),
                exclude_keywords=("Sr.",),
            )
        )
        r = m.match(job(title="Junior PYTHON engineer", exp="NA"))
        self.assertEqual(r.matched_keywords, ("python",))
        self.assertIsNotNone(m.match(job(title="C++ Developer", exp="NA")))
        self.assertIsNotNone(m.match(job(title="C++Developer", exp="NA")))
        self.assertIsNotNone(m.match(job(title="Software   Engineer", exp="NA")))
        self.assertIsNone(m.match(job(title="Sr.Executive Python", exp="NA")))
        self.assertIsNone(m.match(job(title="Digital Marketing Officer", exp="NA")))

    def test_category_and_exclude(self):
        m = UserMatcher(user(exclude_keywords=("Senior",)))
        self.assertIsNone(m.match(job(cats=(9,), exp="NA")))
        self.assertIsNone(m.match(job(title="Senior Python Developer", exp="NA")))

    def test_location_expansion_and_exclusion(self):
        # "Dhaka" should automatically match Dhaka neighborhoods such as "Banglamotor" and "Uttara Sector 12"
        u = user(locations=("Dhaka",), exclude_locations=("Savar", "Gazipur"))
        m = UserMatcher(u)
        j_banglamotor = job(exp="NA")
        j_banglamotor.location = "Banglamotor"
        self.assertIsNotNone(m.match(j_banglamotor))

        j_uttara = job(exp="NA")
        j_uttara.location = "Uttara Sector 12"
        self.assertIsNotNone(m.match(j_uttara))

        j_anywhere = job(exp="NA")
        j_anywhere.location = "Anywhere in Bangladesh"
        self.assertIsNotNone(m.match(j_anywhere))

        j_savar = job(exp="NA")
        j_savar.location = "Savar EPZ"
        self.assertIsNone(m.match(j_savar))

        j_sylhet = job(exp="NA")
        j_sylhet.location = "Sylhet"
        self.assertIsNone(m.match(j_sylhet))

    def test_salary_workplace_job_type_and_required_keywords(self):
        u = user(
            required_keywords=("Python",),
            keywords=("Backend", "Django"),
            job_types=("FullTime",),
            workplaces=("remote",),
            min_salary=30000,
            include_negotiable_salary=True,
        )
        m = UserMatcher(u)
        j = job(title="Python Backend Developer", exp="NA")
        j.job_type = "FullTime"
        j.workplace = "Home"
        j.salary = "Tk. 35000 - 45000 (Monthly)"
        self.assertIsNotNone(m.match(j))

        # Shorthand "40k" salary format
        j.salary = "30k - 40k BDT"
        self.assertIsNotNone(m.match(j))

        # Below min_salary
        j.salary = "Tk. 15000 - 20000 (Monthly)"
        self.assertIsNone(m.match(j))

        # Negotiable salary ("") allowed when include_negotiable_salary=True
        j.salary = ""
        self.assertIsNotNone(m.match(j))

        # Missing required keyword "Python"
        j.title = "Java Backend Developer"
        self.assertIsNone(m.match(j))

    def test_category_resolution_and_validation(self):
        self.assertEqual(resolve_category("IT/Telecommunication"), 8)
        self.assertEqual(resolve_category("marketing"), 9)
        self.assertEqual(resolve_category("8"), 8)
        self.assertFalse(_parse_bool("false", "enabled", "user", True))
        with self.assertRaises(ConfigError):
            _parse_settings({"max_send_attempts": 0})

    def test_parse_api_item(self):
        j = parse_job(
            {
                "Jobid": "123",
                "jobTitle": "Backend \u2014 Engineer",
                "companyName": "X &amp; Y",
                "experience": "NA",
                "deadline": "Dec 07, 2026",
                "deadlineDB": "2026-12-07T00:00:00Z",
                "Cat_id": 8,
                "Salary": "--",
            },
            "https://jobs.example.com/details/{job_id}",
        )
        self.assertEqual(
            (j.job_id, j.title, j.company, j.salary, j.category_ids),
            ("123", "Backend - Engineer", "X & Y", "", {8}),
        )


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.smtp = None
        self.max_attempts = 1

    def send(self, to, content):
        self.sent.append((to, content.subject))


class FakeScraper:
    def __init__(self, jobs, partial_failure=False):
        self.jobs = jobs
        self.settings = Settings()
        self.last_partial_failure = partial_failure

    def fetch(self, cats, is_known=None):
        return {j.job_id: j for j in self.jobs}


class EngineDedup(unittest.TestCase):
    def test_never_notify_twice_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            cfg = AppConfig(
                Path(tmp) / "c.json", Settings(), (user(),), SmtpSettings("a@b.c", "x" * 16)
            )
            jobs = [
                job("1"),
                job("2", title="Backend Engineer", exp="NA"),
                job("3", title="Accountant"),
            ]

            mailer = FakeMailer()
            eng = AlertEngine(cfg, Database(db_path), FakeScraper(jobs), mailer)
            eng.run_cycle()
            self.assertEqual(len(mailer.sent), 2)
            eng.run_cycle()
            self.assertEqual(len(mailer.sent), 2)
            eng.db.close()

            # Simulate restart + re-post of job 1 under a new ID.
            jobs.append(job("99", title="Python  Developer"))
            mailer2 = FakeMailer()
            eng2 = AlertEngine(cfg, Database(db_path), FakeScraper(jobs), mailer2)
            eng2.run_cycle()
            self.assertEqual(mailer2.sent, [])
            eng2.db.close()

    def test_batch_when_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                Path(tmp) / "c.json",
                Settings(instant_batch_threshold=2),
                (user(),),
                SmtpSettings("a@b.c", "x" * 16),
            )
            jobs = [
                job(str(i), title=f"Python Dev {i}", deadline=f"2026-12-{i:02d}")
                for i in range(1, 6)
            ]
            mailer = FakeMailer()
            eng = AlertEngine(cfg, Database(Path(tmp) / "t.db"), FakeScraper(jobs), mailer)
            eng.run_cycle()
            self.assertEqual(len(mailer.sent), 1)
            self.assertIn("5 new matching", mailer.sent[0][1])
            eng.db.close()

    def test_partial_failure_retains_full_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                Path(tmp) / "c.json", Settings(), (user(),), SmtpSettings("a@b.c", "x" * 16)
            )
            eng = AlertEngine(
                cfg,
                Database(Path(tmp) / "t.db"),
                FakeScraper([job("1")], partial_failure=True),
                FakeMailer(),
            )
            report1 = eng.poll()
            self.assertTrue(report1.full_scan)
            self.assertIsNone(eng.db.get_meta("rules_hash"))
            eng.scraper.last_partial_failure = False
            report2 = eng.poll()
            self.assertTrue(report2.full_scan)
            self.assertIsNotNone(eng.db.get_meta("rules_hash"))
            eng.db.close()

    def test_rule_change_clears_stale_pending_notifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            u1 = user(keywords=("Python", "Java"), schedule=ScheduleRule("digest", ("09:00",)))
            cfg1 = AppConfig(
                Path(tmp) / "c.json", Settings(), (u1,), SmtpSettings("a@b.c", "x" * 16)
            )
            jobs = [job("1", title="Python Dev"), job("2", title="Java Dev")]
            eng = AlertEngine(cfg1, Database(db_path), FakeScraper(jobs), FakeMailer())
            eng.poll()
            self.assertEqual(eng.db.stats().get("notifications_pending"), 2)

            # Now user narrows keywords to only Python; stale Java pending alert must be removed
            u2 = user(keywords=("Python",), schedule=ScheduleRule("digest", ("09:00",)))
            cfg2 = AppConfig(
                Path(tmp) / "c.json", Settings(), (u2,), SmtpSettings("a@b.c", "x" * 16)
            )
            eng.apply_config(cfg2)
            eng.poll()
            self.assertEqual(eng.db.stats().get("notifications_pending"), 1)
            eng.db.close()

    def test_scrape_failure_does_not_crash_and_still_dispatches_pending(self):
        class FailingScraper(FakeScraper):
            def fetch(self, cats, is_known=None):
                raise ScrapeError("simulated network outage")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            cfg = AppConfig(
                Path(tmp) / "c.json", Settings(), (user(),), SmtpSettings("a@b.c", "x" * 16)
            )
            db = Database(db_path)
            db.upsert_jobs([job("1", title="Python Dev")])
            uid = db.sync_users(cfg.users)["t@example.com"]
            db.enqueue_many(uid, [(job("1", title="Python Dev"), ("Python",))])

            mailer = FakeMailer()
            eng = AlertEngine(cfg, db, FailingScraper([]), mailer)
            report = eng.poll()
            self.assertIsNotNone(report.error)
            # Even when poll fails due to network outage, run_cycle must not crash and must dispatch pending alerts
            eng.run_cycle()
            self.assertEqual(len(mailer.sent), 1)
            eng.db.close()


class ManageCLI(unittest.TestCase):
    def test_user_and_db_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            db_path = Path(tmp) / "jobs.db"
            cfg_path.write_text(
                f'{{"settings": {{"db_path": "{db_path}"}}, "users": []}}\n',
                encoding="utf-8",
            )
            base = ["--config", str(cfg_path), "--env", "/dev/null"]

            # Add user
            rc = manage_main(
                base
                + [
                    "add-user",
                    "--name",
                    "Alice",
                    "--email",
                    "alice@example.com",
                    "--categories",
                    "IT/Telecommunication",
                    "--min-years",
                    "0",
                    "--max-years",
                    "2",
                    "--keywords",
                    "Python,Django",
                    "--mode",
                    "instant",
                ]
            )
            self.assertEqual(rc, 0)

            # Edit user to digest
            rc = manage_main(
                base
                + [
                    "edit-user",
                    "alice@example.com",
                    "--mode",
                    "digest",
                    "--digest-time",
                    "09:00,18:00",
                    "--disable",
                ]
            )
            self.assertEqual(rc, 0)

            # Enable user
            self.assertEqual(manage_main(base + ["enable-user", "alice@example.com"]), 0)

            # Insert a job and notification, then test reset-user and clear-db
            db = Database(db_path)
            db.upsert_jobs([job("10")])
            uid = db.sync_users([user(email="alice@example.com")])["alice@example.com"]
            db.enqueue_many(uid, [(job("10"), ("Python",))])
            self.assertEqual(db.stats().get("notifications_pending"), 1)
            db.close()

            self.assertEqual(manage_main(base + ["reset-user", "alice@example.com", "-y"]), 0)
            self.assertEqual(manage_main(base + ["clear-db", "-y"]), 0)
            self.assertEqual(manage_main(base + ["remove-user", "alice@example.com", "-y"]), 0)

            db2 = Database(db_path)
            self.assertEqual(db2.stats()["jobs"], 0)
            self.assertEqual(db2.stats()["users"], 0)
            db2.close()


if __name__ == "__main__":
    unittest.main()
