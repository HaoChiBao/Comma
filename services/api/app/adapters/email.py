import hashlib
import hmac
import smtplib
from email.message import EmailMessage

from app.adapters.photon import SendResult, _accept, _lookup, delivery_hook
from app.errors import IntegrationNotConfigured
from app.settings import settings


def verify_email_signature(raw_body: bytes, signature: str | None) -> None:
    secret = settings.email_webhook_secret
    if not secret:
        raise IntegrationNotConfigured("EMAIL_WEBHOOK_SECRET is not configured; refusing unauthenticated email webhooks")
    if not signature:
        raise PermissionError("missing email signature")
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if len(expected) != len(signature) or not hmac.compare_digest(expected, signature):
        raise PermissionError("bad email signature")


class EmailAdapter:
    def send_email(self, *, to: str, subject: str, body: str, company_id, idempotency_key: str) -> SendResult:
        raise NotImplementedError

    def reconcile(self, idempotency_key: str) -> SendResult:
        raise NotImplementedError


class SandboxEmailAdapter(EmailAdapter):
    """Stores outbound mail in the local sandbox ledger. Nothing is delivered to a real inbox."""

    def send_email(self, *, to: str, subject: str, body: str, company_id, idempotency_key: str) -> SendResult:
        mode = delivery_hook.get("email")
        delivery_hook["email"] = None
        if mode == "fail":
            return SendResult(status="failed", error="sandbox smtp 451 temporary failure")
        row, duplicate = _accept(
            "email",
            idempotency_key,
            company_id,
            {"to": to, "subject": subject, "body": body},
        )
        if mode == "ambiguous":
            return SendResult(status="ambiguous")
        return SendResult(status="sent", provider_ref=row.provider_ref, duplicate=duplicate)

    def reconcile(self, idempotency_key: str) -> SendResult:
        row = _lookup(idempotency_key)
        if row is None:
            return SendResult(status="unknown")
        return SendResult(status="sent", provider_ref=row.provider_ref, duplicate=True)


class SmtpEmailAdapter(EmailAdapter):
    def send_email(self, *, to: str, subject: str, body: str, company_id, idempotency_key: str) -> SendResult:
        if not settings.smtp_host or not settings.smtp_username:
            raise IntegrationNotConfigured("EMAIL_MODE=smtp requires SMTP_HOST and SMTP_USERNAME")
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return SendResult(status="sent", provider_ref=f"smtp-{idempotency_key}")

    def reconcile(self, idempotency_key: str) -> SendResult:
        return SendResult(status="unknown", error="smtp reconcile is not supported; leaving the outbox ambiguous")


def email_adapter() -> EmailAdapter:
    if settings.email_mode == "smtp":
        return SmtpEmailAdapter()
    return SandboxEmailAdapter()
