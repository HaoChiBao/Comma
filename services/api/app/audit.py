import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.clock import clock
from app.models import WorkEvent


def record(
    session: Session,
    *,
    company_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    actor_type: str,
    actor_id: uuid.UUID | None,
    action: str,
    previous_state: str | None,
    new_state: str | None,
    reason: str | None,
    diff: dict | None = None,
    evidence_refs: list | None = None,
    allowed: bool,
) -> WorkEvent:
    refs = [ref for ref in (evidence_refs or []) if ref is not None and ref != ""]
    event = WorkEvent(
        id=uuid.uuid4(),
        company_id=company_id,
        task_id=task_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason,
        diff_json=diff or {},
        evidence_refs_json=refs,
        allowed=allowed,
        created_at=clock.now(),
    )
    session.add(event)
    return event
