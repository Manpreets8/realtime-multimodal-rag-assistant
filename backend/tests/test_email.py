"""Welcome email: content, delivery through a real (local) SMTP server, and failure handling."""

import email
import logging
import socket
import uuid
from collections.abc import Iterator
from email.message import EmailMessage
from email.policy import default as default_policy

import pytest
from aiosmtpd.controller import Controller
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.services import email_service

AUTH = "/api/v1/auth"
PASSWORD = "correct-horse-42"


class Inbox:
    """aiosmtpd handler that stores received messages, or rejects every recipient."""

    def __init__(self, reject_with: str | None = None) -> None:
        self.messages: list[tuple[list[str], EmailMessage]] = []
        self.reject_with = reject_with

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if self.reject_with:
            return self.reject_with
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        parsed = email.message_from_bytes(envelope.content, policy=default_policy)
        self.messages.append((list(envelope.rcpt_tos), parsed))
        return "250 Message accepted for delivery"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _use_smtp(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", port)
    monkeypatch.setattr(settings, "smtp_security", "none")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "email_from", "Mindora AI <hello@mindora.test>")
    monkeypatch.setattr(settings, "smtp_timeout_seconds", 5.0)
    monkeypatch.setattr(email_service, "RETRY_DELAYS_SECONDS", (0.0, 0.0))


def _start(inbox: Inbox) -> Iterator[int]:
    port = _free_port()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield port
    finally:
        controller.stop()


@pytest.fixture
def inbox() -> Inbox:
    return Inbox()


@pytest.fixture
def smtp_server(inbox: Inbox, monkeypatch: pytest.MonkeyPatch) -> Iterator[Inbox]:
    for port in _start(inbox):
        _use_smtp(monkeypatch, port)
        yield inbox


def _text(message: EmailMessage, subtype: str) -> str:
    part = message.get_body(preferencelist=(subtype,))
    assert part is not None
    return part.get_content()


# --- Content ------------------------------------------------------------------


def test_welcome_email_has_text_and_html_with_description_and_link() -> None:
    settings = Settings(
        _env_file=None,
        email_from="Mindora AI <hello@mindora.test>",
        app_public_url="https://mindora.example/",
    )

    message = email_service.build_welcome_email("ada@example.com", "Ada", settings)

    assert message["Subject"] == "Welcome to Mindora AI"
    assert message["To"] == "ada@example.com"
    assert message["From"] == "Mindora AI <hello@mindora.test>"
    for part in (_text(message, "plain"), _text(message, "html")):
        body = " ".join(part.split())  # HTML source wraps lines; clients render it as spaces
        assert "Hi Ada," in body
        assert "answers questions from your own documents" in body
        assert "https://mindora.example" in body


def test_welcome_email_escapes_the_name_in_html_and_greets_generically_without_one() -> None:
    settings = Settings(_env_file=None, email_from="hello@mindora.test")

    html_body = _text(email_service.build_welcome_email("x@example.com", "<b>Eve</b>", settings), "html")
    assert "&lt;b&gt;Eve&lt;/b&gt;" in html_body
    assert "<b>Eve</b>" not in html_body

    assert "Hi there," in _text(email_service.build_welcome_email("x@example.com", None, settings), "plain")


def test_sender_is_required_when_smtp_is_configured() -> None:
    with pytest.raises(ValidationError, match="EMAIL_FROM"):
        Settings(_env_file=None, smtp_host="smtp.example.com", smtp_username="", email_from="")

    settings = Settings(_env_file=None, smtp_host="smtp.example.com", smtp_username="me@example.com")
    assert settings.email_sender == "me@example.com"


# --- Delivery -----------------------------------------------------------------


async def test_sign_up_delivers_the_welcome_email(
    client: AsyncClient, db: AsyncSession, smtp_server: Inbox
) -> None:
    response = await client.post(
        f"{AUTH}/register",
        json={"email": "Ada@Example.com", "password": PASSWORD, "full_name": "Ada Lovelace"},
    )

    assert response.status_code == 201
    assert len(smtp_server.messages) == 1
    recipients, message = smtp_server.messages[0]
    assert recipients == ["ada@example.com"]
    assert message["Subject"] == "Welcome to Mindora AI"
    assert "Hi Ada Lovelace," in _text(message, "plain")


async def test_failed_sign_up_sends_no_email(
    client: AsyncClient, db: AsyncSession, smtp_server: Inbox
) -> None:
    await client.post(f"{AUTH}/register", json={"email": "dup@example.com", "password": PASSWORD})
    response = await client.post(f"{AUTH}/register", json={"email": "dup@example.com", "password": PASSWORD})

    assert response.status_code == 409
    assert len(smtp_server.messages) == 1


async def test_sign_up_succeeds_when_the_mail_server_is_down(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _use_smtp(monkeypatch, _free_port())  # nothing listens there
    caplog.set_level(logging.INFO)

    response = await client.post(f"{AUTH}/register", json={"email": "down@example.com", "password": PASSWORD})

    assert response.status_code == 201
    attempts = [r for r in caplog.records if r.getMessage() == "welcome_email_attempt_failed"]
    assert len(attempts) == 3  # temporary failure: retried
    assert any(r.getMessage() == "welcome_email_failed" for r in caplog.records)
    assert all("down@example.com" not in r.getMessage() + str(r.__dict__) for r in caplog.records)


async def test_permanent_rejection_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    for port in _start(Inbox(reject_with="550 No such user")):
        _use_smtp(monkeypatch, port)
        await email_service.send_welcome_email(uuid.uuid4(), "nobody@example.com", None)

    attempts = [r for r in caplog.records if r.getMessage() == "welcome_email_attempt_failed"]
    assert len(attempts) == 1
    assert attempts[0].permanent is True
    assert any(r.getMessage() == "welcome_email_failed" for r in caplog.records)


async def test_email_is_skipped_when_smtp_is_not_configured(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    await email_service.send_welcome_email(uuid.uuid4(), "someone@example.com", None)

    assert any(r.getMessage() == "welcome_email_skipped" for r in caplog.records)
