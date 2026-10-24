import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import clock
from app.db import SessionLocal
from app.errors import IntegrationNotConfigured
from app.models import SandboxDelivery
from app.settings import settings

TOLERANCE_SEC = 5 * 60


class SignatureError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class InboundMessage:
    provider_message_id: str
    sender: str
    recipient: str | None
    text: str
    content_type: str
    sent_at: datetime | None
    raw: dict


@dataclass
class SendResult:
    status: str
    provider_ref: str | None = None
    duplicate: bool = False
    error: str | None = None


# One-shot test hook: "fail" or "ambiguous". Never set in production paths.
delivery_hook: dict[str, str | None] = {"next": None, "email": None}


def verify_signature(raw_body: bytes, timestamp: str | None, signature: str | None, secret: str, now: float | None = None) -> None:
    """Spectrum Stable webhook verifier (photon.codes/docs/webhooks/verifying-signatures)."""
    if not secret:
        raise SignatureError(401, "webhook secret is not configured")
    if not timestamp or not signature:
        raise SignatureError(400, "missing signature headers")
    try:
        stamp = int(timestamp)
    except ValueError as exc:
        raise SignatureError(400, "invalid timestamp") from exc
    current = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    if abs(current - stamp) > TOLERANCE_SEC:
        raise SignatureError(400, "stale timestamp")
    digest = hmac.new(secret.encode(), f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode(), hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    if len(expected) != len(signature) or not hmac.compare_digest(expected, signature):
        raise SignatureError(401, "bad signature")


def parse_inbound(payload: dict, headers: dict) -> InboundMessage:
    message = payload.get("message") or {}
    space = payload.get("space") or message.get("space") or {}
    content = message.get("content") or {}
    sender = (message.get("sender") or {}).get("id") or ""
    text = content.get("text") if content.get("type") == "text" else ""
    sent_at = None
    if message.get("timestamp"):
        sent_at = datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
    return InboundMessage(
        provider_message_id=str(message.get("id") or ""),
        sender=str(sender),
        recipient=space.get("phone"),
        text=text or "",
        content_type=str(content.get("type") or ""),
        sent_at=sent_at,
        raw={"headers": {k: v for k, v in headers.items() if k.lower().startswith("x-spectrum")}, "payload": payload},
    )


def verify_and_parse(raw_body: bytes, payload: dict, headers: dict) -> InboundMessage:
    lowered = {k.lower(): v for k, v in headers.items()}
    verify_signature(
        raw_body,
        lowered.get("x-spectrum-timestamp"),
        lowered.get("x-spectrum-signature"),
        settings.photon_webhook_secret,
    )
    inbound = parse_inbound(payload, lowered)
    if not inbound.provider_message_id:
        raise SignatureError(400, "missing message id")
    return inbound


class MessagingAdapter:
    def send_message(self, *, to: str, text: str, correlation_id: str, company_id, idempotency_key: str) -> SendResult:
        raise NotImplementedError


class MockMessagingAdapter(MessagingAdapter):
    """Local iMessage stand-in. Writes a sandbox ledger row. Does not contact Apple or Photon."""

    channel = "photon"

    def send_message(self, *, to: str, text: str, correlation_id: str, company_id, idempotency_key: str) -> SendResult:
        mode = delivery_hook.get("next")
        delivery_hook["next"] = None
        if mode == "fail":
            return SendResult(status="failed", error="mock photon transport failed before accept")
        row, duplicate = _accept(self.channel, idempotency_key, company_id, {"to": to, "text": text, "correlation_id": correlation_id})
        if mode == "ambiguous":
            return SendResult(status="ambiguous")
        return SendResult(status="sent", provider_ref=row.provider_ref, duplicate=duplicate)

    def reconcile(self, idempotency_key: str) -> SendResult:
        row = _lookup(idempotency_key)
        if row is None:
            return SendResult(status="unknown")
        return SendResult(status="sent", provider_ref=row.provider_ref, duplicate=True)


class PhotonHQAdapter(MessagingAdapter):
    """Live Photon/Spectrum adapter.

    Stable docs (photon.codes/docs, not beta) verify inbound HMAC signatures and state
    there is no public HTTP send endpoint. Outbound delivery requires the spectrum-ts
    SDK (`space.send`) plus project credentials. This process does not open that SDK
    session, so live send fails closed instead of reporting a delivery.
    """

    def send_message(self, *, to: str, text: str, correlation_id: str, company_id, idempotency_key: str) -> SendResult:
        if not settings.photon_api_key or not settings.photon_project_id:
            raise IntegrationNotConfigured(
                "PHOTON_MODE=live needs PHOTON_PROJECT_ID and PHOTON_API_KEY. "
                "No public HTTP send API is documented, and no spectrum-ts session is configured."
            )
        raise IntegrationNotConfigured(
            "Live iMessage send is not available in this deployment. "
            "Spectrum Stable docs require space.send via spectrum-ts; refusing to mark the message delivered."
        )

    def reconcile(self, idempotency_key: str) -> SendResult:
        raise IntegrationNotConfigured("Live Photon reconcile requires a spectrum-ts session, which is not configured.")


def messaging_adapter() -> MessagingAdapter:
    if settings.photon_mode == "live":
        return PhotonHQAdapter()
    return MockMessagingAdapter()


def _accept(channel: str, idempotency_key: str, company_id, payload: dict) -> tuple[SandboxDelivery, bool]:
    session = SessionLocal()
    try:
        existing = session.scalar(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == idempotency_key))
        if existing:
            return existing, True
        row = SandboxDelivery(
            id=uuid.uuid4(),
            company_id=company_id,
            channel=channel,
            idempotency_key=idempotency_key,
            provider_ref=f"sandbox-{channel}-{uuid.uuid4().hex[:12]}",
            payload_json={**payload, "simulated": True, "delivered_to_network": False},
            created_at=clock.now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row, False
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing, True
    finally:
        session.close()


def _lookup(idempotency_key: str) -> SandboxDelivery | None:
    session = SessionLocal()
    try:
        return session.scalar(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == idempotency_key))
    finally:
        session.close()
