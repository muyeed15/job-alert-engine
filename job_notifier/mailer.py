"""SMTP email dispatch (SSL :465 or STARTTLS :587) with connection reuse and exponential backoff."""

from __future__ import annotations

import logging
import random
import re
import smtplib
import socket
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from .config import SmtpSettings
from .templates import EmailContent

log = logging.getLogger(__name__)

# SMTP reply codes worth retrying: service unavailable, mailbox busy, local error, rate limit.
_TRANSIENT_CODES = {421, 450, 451, 452, 454}
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class MailError(RuntimeError):
    """``permanent``: retrying will not help for this message.
    ``abort_cycle``: an account-level problem (bad credentials, daily quota) - stop dispatching
    for this cycle without penalizing individual notifications."""

    def __init__(self, message: str, permanent: bool = False, abort_cycle: bool = False):
        super().__init__(message)
        self.permanent = permanent
        self.abort_cycle = abort_cycle


def _clean_header(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text).strip()


def _is_account_level(exc: BaseException) -> bool:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return True
    if isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code >= 500:
        text = (
            exc.smtp_error.decode(errors="ignore")
            if isinstance(exc.smtp_error, bytes)
            else str(exc.smtp_error)
        )
        return "5.4.5" in text or "quota" in text.lower() or "limit" in text.lower()
    return False


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return False
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code in _TRANSIENT_CODES
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = {code for code, _ in exc.recipients.values()}
        return bool(codes) and codes <= _TRANSIENT_CODES
    return isinstance(
        exc,
        (
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            socket.timeout,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    )


class Mailer:
    def __init__(
        self,
        smtp: SmtpSettings,
        max_attempts: int = 5,
        base_delay: float = 2.0,
        max_delay: float = 300.0,
    ):
        self.smtp = smtp
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._active_server: smtplib.SMTP | None = None
        self._in_session = False

    def _build_message(self, to: str, content: EmailContent) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = _clean_header(content.subject)
        msg["From"] = formataddr(
            (_clean_header(self.smtp.from_name), _clean_header(self.smtp.user))
        )
        msg["To"] = _clean_header(to)
        msg["Date"] = formatdate(localtime=True)
        domain = self.smtp.user.split("@")[-1].strip() if "@" in self.smtp.user else ""
        msg["Message-ID"] = make_msgid(domain=domain if _DOMAIN_RE.match(domain) else None)
        msg["X-Mailer"] = "job-alert-engine"
        msg.attach(MIMEText(content.text, "plain", "utf-8"))
        msg.attach(MIMEText(content.html, "html", "utf-8"))
        return msg

    def _connect(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        try:
            import certifi

            ctx.load_verify_locations(certifi.where())
        except Exception:  # pragma: no cover
            pass
        timeout = self.smtp.timeout_seconds
        if self.smtp.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.smtp.host, self.smtp.port, timeout=timeout, context=ctx
            )
        else:
            server = smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=timeout)
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
        server.login(self.smtp.user, self.smtp.app_password)
        return server

    def _close_active(self) -> None:
        if self._active_server is not None:
            try:
                self._active_server.quit()
            except Exception:  # noqa: BLE001
                pass
            self._active_server = None

    @contextmanager
    def session(self) -> Iterator[Mailer]:
        """Reuse a single authenticated SMTP connection across multiple send() calls."""
        if self._in_session:
            yield self
            return
        self._in_session = True
        try:
            yield self
        finally:
            self._in_session = False
            self._close_active()

    def send(self, to: str, content: EmailContent) -> None:
        """Send one email, retrying transient failures with exponential backoff + jitter."""
        if not self.smtp.configured:
            raise MailError(
                "GMAIL_USER / GMAIL_APP_PASSWORD are not set", permanent=True, abort_cycle=True
            )
        try:
            msg = self._build_message(to, content)
            raw_msg = msg.as_string()
        except Exception as exc:  # noqa: BLE001
            raise MailError(f"Failed to build email for {to}: {exc}", permanent=True) from exc
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            server = None
            try:
                if self._in_session:
                    if self._active_server is None:
                        self._active_server = self._connect()
                    server = self._active_server
                else:
                    server = self._connect()
                server.sendmail(self.smtp.user, [to], raw_msg)
                log.info("Email sent to %s: %s", to, content.subject)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self._in_session:
                    self._close_active()
                    server = None
                if _is_account_level(exc):
                    raise MailError(
                        f"SMTP account problem: {exc}", permanent=True, abort_cycle=True
                    ) from exc
                if not _is_transient(exc):
                    raise MailError(
                        f"Permanent SMTP failure for {to}: {exc}", permanent=True
                    ) from exc
                if attempt == self.max_attempts:
                    break
                delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, delay * 0.25)
                log.warning(
                    "SMTP attempt %d/%d to %s failed (%s); retrying in %.0fs",
                    attempt,
                    self.max_attempts,
                    to,
                    exc,
                    delay,
                )
                time.sleep(delay)
            finally:
                if not self._in_session and server is not None:
                    try:
                        server.quit()
                    except Exception:  # noqa: BLE001
                        pass
        raise MailError(f"SMTP failed after {self.max_attempts} attempts for {to}: {last_exc}")
