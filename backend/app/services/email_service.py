"""Transactional email over SMTP (any provider: Gmail, Brevo, SES, Mailgun...).

Emails are sent after the response, in the background: a slow or unreachable mail server
never delays or fails the request that triggered it. Failures are logged, not raised.
"""

import asyncio
import html
import logging
import uuid
from email.message import EmailMessage

import aiosmtplib

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Waits before the 2nd and 3rd attempts. Only temporary failures (connection problems,
# timeouts, 4xx replies) are retried; a 5xx reply such as bad credentials is not.
RETRY_DELAYS_SECONDS: tuple[float, ...] = (5.0, 30.0)

_BRAND_COLOR = "#4f46e5"
# Inline styles: most email clients ignore <style> blocks and external CSS.
_PAGE = "margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a;"
_OUTER = "background:#f1f5f9;padding:32px 16px;"
_CARD = "max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;"
_HEADER = f"background:{_BRAND_COLOR};padding:20px 28px;color:#ffffff;font-size:20px;font-weight:bold;"
_BODY = "padding:28px;font-size:15px;line-height:1.6;"
_P = "margin:0 0 16px;"
_BUTTON = (
    f"display:inline-block;background:{_BRAND_COLOR};color:#ffffff;text-decoration:none;"
    "padding:12px 22px;border-radius:8px;font-weight:bold;"
)
_FOOTER = "padding:16px 28px;border-top:1px solid #e2e8f0;font-size:12px;color:#64748b;"


def build_welcome_email(to: str, full_name: str | None, settings: Settings) -> EmailMessage:
    """A plain-text welcome email with an HTML alternative (both say the same thing)."""
    greeting_name = full_name.strip() if full_name and full_name.strip() else "there"
    product = settings.app_name
    link = settings.app_public_url.rstrip("/")

    message = EmailMessage()
    message["Subject"] = f"Welcome to {product}"
    message["From"] = settings.email_sender
    message["To"] = to

    message.set_content(
        f"Hi {greeting_name},\n\n"
        f"Welcome to {product}! Your account is ready.\n\n"
        f"{product} is an AI assistant that answers questions from your own documents. "
        "Upload PDFs, Word files or notes into a knowledge base, then ask questions in plain "
        "language, by typing, speaking or sharing an image. Every answer cites the exact "
        "passages it came from, so you can check it.\n\n"
        "Get started:\n"
        "  1. Create a knowledge base\n"
        "  2. Upload your documents\n"
        "  3. Ask your first question\n\n"
        f"Open {product}: {link}\n\n"
        f"You received this email because this address was used to sign up for {product}. "
        "If that wasn't you, you can ignore it.\n"
    )

    name = html.escape(greeting_name)
    href = html.escape(link, quote=True)
    product_html = html.escape(product)
    message.add_alternative(
        f"""<!doctype html>
<html>
<body style="{_PAGE}">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="{_OUTER}">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="{_CARD}">
        <tr><td style="{_HEADER}">{product_html}</td></tr>
        <tr><td style="{_BODY}">
          <p style="{_P}">Hi {name},</p>
          <p style="{_P}">Welcome to {product_html}! Your account is ready.</p>
          <p style="{_P}">{product_html} is an AI assistant that answers questions from your own
            documents. Upload PDFs, Word files or notes into a knowledge base, then ask questions in
            plain language, by typing, speaking or sharing an image. Every answer cites the exact
            passages it came from, so you can check it.</p>
          <p style="margin:0 0 8px;font-weight:bold;">Get started:</p>
          <ol style="margin:0 0 24px;padding-left:20px;">
            <li>Create a knowledge base</li>
            <li>Upload your documents</li>
            <li>Ask your first question</li>
          </ol>
          <a href="{href}" style="{_BUTTON}">Open {product_html}</a>
        </td></tr>
        <tr><td style="{_FOOTER}">
          You received this email because this address was used to sign up for {product_html}.
          If that wasn't you, you can ignore it.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""",
        subtype="html",
    )
    return message


async def _send(message: EmailMessage, settings: Settings) -> None:
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host.strip(),
        port=settings.smtp_port,
        username=settings.smtp_username.strip() or None,
        password=password,
        use_tls=settings.smtp_security == "ssl",
        start_tls=settings.smtp_security == "starttls",
        timeout=settings.smtp_timeout_seconds,
    )


def _is_permanent(exc: Exception) -> bool:
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):  # carries one reply per recipient
        return bool(exc.recipients) and all(refused.code >= 500 for refused in exc.recipients)
    return isinstance(exc, aiosmtplib.SMTPResponseException) and exc.code >= 500


async def send_welcome_email(user_id: uuid.UUID, to: str, full_name: str | None) -> None:
    """Send the sign-up welcome email. Never raises: this runs after the response is sent."""
    settings = get_settings()
    log_extra = {"user_id": str(user_id)}  # never the address itself
    if not settings.email_enabled:
        logger.info("welcome_email_skipped", extra={**log_extra, "reason": "smtp_not_configured"})
        return

    message = build_welcome_email(to, full_name, settings)
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_SECONDS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await _send(message, settings)
        except (aiosmtplib.SMTPException, OSError) as exc:
            permanent = _is_permanent(exc)
            logger.warning(
                "welcome_email_attempt_failed",
                extra={
                    **log_extra,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "permanent": permanent,
                },
            )
            if permanent:
                break
        else:
            logger.info("welcome_email_sent", extra={**log_extra, "attempt": attempt})
            return
    logger.error("welcome_email_failed", extra=log_extra)
