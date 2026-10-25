from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.temporal import canonical, sha256_text


def append_event(db: Session, organization_id: str, event_type: str, subject_id: str, payload: dict, actor_id: str | None) -> AuditEvent:
    previous = db.scalar(
        select(AuditEvent).where(AuditEvent.organization_id == organization_id).order_by(AuditEvent.timestamp.desc())
    )
    previous_hash = previous.event_hash if previous else "0" * 64
    timestamp = datetime.now(timezone.utc)
    event_id = uuid4().hex
    body = canonical(
        {
            "id": event_id,
            "organization_id": organization_id,
            "event_type": event_type,
            "subject_id": subject_id,
            "payload": payload,
            "actor_id": actor_id,
            "timestamp": timestamp.isoformat(),
            "previous_event_hash": previous_hash,
        }
    )
    event = AuditEvent(
        id=event_id,
        organization_id=organization_id,
        event_type=event_type,
        subject_id=subject_id,
        payload_json=payload,
        actor_id=actor_id,
        timestamp=timestamp,
        previous_event_hash=previous_hash,
        event_hash=sha256_text(body),
    )
    db.add(event)
    return event
