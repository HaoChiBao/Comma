from sqlalchemy import update
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import clock
from app.models import ProcurementTask

TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"NEEDS_INFO", "SOURCING", "CANCELLED"},
    "NEEDS_INFO": {"SOURCING", "CANCELLED", "BLOCKED"},
    "SOURCING": {"AWAITING_QUOTES", "EVALUATING", "BLOCKED", "CANCELLED"},
    "AWAITING_QUOTES": {"EVALUATING", "SOURCING", "BLOCKED", "CANCELLED"},
    "EVALUATING": {"AWAITING_APPROVAL", "BLOCKED", "SOURCING", "AWAITING_QUOTES", "CANCELLED"},
    "AWAITING_APPROVAL": {"ORDERING", "EVALUATING", "BLOCKED", "SOURCING", "CANCELLED"},
    "ORDERING": {"AWAITING_CONFIRMATION", "BLOCKED", "CANCELLED", "EVALUATING"},
    "AWAITING_CONFIRMATION": {"COMPLETED", "EVALUATING", "BLOCKED", "SOURCING", "CANCELLED"},
    "COMPLETED": set(),
    "BLOCKED": {"SOURCING", "EVALUATING", "AWAITING_QUOTES", "AWAITING_APPROVAL", "NEEDS_INFO", "CANCELLED"},
    "CANCELLED": set(),
}


class InvalidTransition(Exception):
    def __init__(self, previous: str, new: str):
        super().__init__(f"invalid transition {previous} -> {new}")
        self.previous = previous
        self.new = new


class ConcurrentModification(Exception):
    pass


def transition(
    session: Session,
    task: ProcurementTask,
    new_status: str,
    *,
    actor_type: str,
    actor_id,
    reason: str,
    evidence_refs: list | None = None,
    diff: dict | None = None,
    action: str = "task_transition",
) -> None:
    previous = task.status
    if new_status == previous:
        return
    refs = [ref for ref in (evidence_refs or []) if ref]
    if not refs:
        refs = [str(task.id)]
    if new_status not in TRANSITIONS.get(previous, set()):
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="transition_denied",
            previous_state=previous,
            new_state=new_status,
            reason=reason or f"invalid transition {previous} -> {new_status}",
            diff={"status": {"from": previous, "to": new_status}},
            evidence_refs=refs,
            allowed=False,
        )
        raise InvalidTransition(previous, new_status)
    result = session.execute(
        update(ProcurementTask)
        .where(ProcurementTask.id == task.id, ProcurementTask.version == task.version)
        .values(status=new_status, version=task.version + 1, updated_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="transition_denied",
            previous_state=previous,
            new_state=new_status,
            reason="concurrent modification",
            evidence_refs=refs,
            allowed=False,
        )
        raise ConcurrentModification(str(task.id))
    task.status = new_status
    task.version = task.version + 1
    task.updated_at = clock.now()
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        previous_state=previous,
        new_state=new_status,
        reason=reason,
        diff=diff or {"status": {"from": previous, "to": new_status}},
        evidence_refs=refs,
        allowed=True,
    )
