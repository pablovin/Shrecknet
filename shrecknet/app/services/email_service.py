from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config_store import Settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


_email_service_status: dict[str, object] = {
    "configured": False,
    "checked_at": None,
    "error": "Email service has not yet been checked.",
}


def get_email_service_status() -> dict[str, object]:
    """Return the current, admin-safe SMTP readiness result."""
    return dict(_email_service_status)


class EmailService:
    """Small SMTP adapter kept independent from registration business logic."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_verification(self, *, recipient: str, username: str, verification_url: str) -> None:
        values = {"username": username, "email": recipient, "verification_url": verification_url}
        message = EmailMessage()
        message["Subject"] = _render(self.settings.email_verification_subject, values)
        message["From"] = formataddr((self.settings.smtp_sender_name, self.settings.smtp_sender_email))
        message["To"] = recipient
        message.set_content(_render(self.settings.email_verification_text_template, values))
        message.add_alternative(_render(self.settings.email_verification_html_template, values), subtype="html")
        try:
            await asyncio.to_thread(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Unable to deliver verification email") from exc

    async def send_message(self, *, recipient: str, subject: str, message: str) -> None:
        """Send a trusted service's plain-text message to one stored user email."""
        email = EmailMessage()
        email["Subject"] = subject
        email["From"] = formataddr((self.settings.smtp_sender_name, self.settings.smtp_sender_email))
        email["To"] = recipient
        email.set_content(message)
        try:
            await asyncio.to_thread(self._send, email)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Unable to deliver email") from exc

    async def verify_connection(self) -> None:
        """Open, secure, and authenticate an SMTP connection without sending mail."""
        try:
            await asyncio.to_thread(self._verify_connection)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Unable to connect to SMTP server") from exc

    async def validate_and_record_status(self) -> dict[str, object]:
        """Run the SMTP readiness check and retain no sensitive failure detail."""
        checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        try:
            self._validate_required_settings()
            await self.verify_connection()
        except (EmailDeliveryError, ValueError) as exc:
            reason = _safe_failure_reason(exc)
            _email_service_status.update(
                configured=False,
                checked_at=checked_at,
                error=reason,
            )
            logger.warning("SMTP readiness validation failed: %s", reason)
        else:
            _email_service_status.update(configured=True, checked_at=checked_at, error=None)
        return get_email_service_status()

    def _send(self, message: EmailMessage) -> None:
        with self._smtp_connection() as smtp:
            smtp.send_message(message)

    def _verify_connection(self) -> None:
        with self._smtp_connection() as smtp:
            code, _ = smtp.noop()
            if code != 250:
                raise smtplib.SMTPException("SMTP NOOP was not accepted")

    def _validate_required_settings(self) -> None:
        if not str(self.settings.smtp_host).strip():
            raise ValueError("SMTP host is required.")
        if not self.settings.smtp_port:
            raise ValueError("SMTP port is required.")
        if self.settings.smtp_username and not self.settings.smtp_password:
            raise ValueError("SMTP password is required when SMTP username is configured.")
        if self.settings.smtp_password and not self.settings.smtp_username:
            raise ValueError("SMTP username is required when SMTP password is configured.")
        if self.settings.smtp_tls_mode not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP TLS mode must be starttls, ssl, or none.")

    def _smtp_connection(self) -> smtplib.SMTP:
        tls_mode = self.settings.smtp_tls_mode
        if tls_mode == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=5)
        else:
            smtp = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=5)
        if tls_mode == "starttls":
            smtp.starttls()
        if self.settings.smtp_username:
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
        return smtp


def _render(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def _safe_failure_reason(exc: EmailDeliveryError | ValueError) -> str:
    """Describe readiness failures without exposing hostnames, usernames, or secrets."""
    if isinstance(exc, ValueError):
        return str(exc)

    cause = exc.__cause__
    if isinstance(cause, smtplib.SMTPAuthenticationError):
        return "SMTP authentication failed. Check the username and password."
    if isinstance(cause, ssl.SSLError):
        return "SMTP TLS negotiation failed. Check the selected TLS mode and server certificate."
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "SMTP connection timed out. Check the host, port, and network reachability."
    if isinstance(cause, ConnectionRefusedError):
        return "SMTP connection was refused. Check the host, port, and whether the server accepts connections."
    if isinstance(cause, socket.gaierror):
        return "SMTP host could not be resolved. Check the SMTP host name."
    if isinstance(cause, smtplib.SMTPServerDisconnected):
        return "SMTP server disconnected during validation. Check TLS mode and server availability."
    if isinstance(cause, smtplib.SMTPException):
        return "SMTP server rejected validation. Check TLS mode, authentication, and server policy."
    if isinstance(cause, OSError):
        return "Could not connect to the SMTP server. Check the host, port, and network reachability."
    return "SMTP validation failed. Check the SMTP configuration and try again."
