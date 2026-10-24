import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import clock
from app.models import WorkerJob
from app.settings import settings


def enqueue_advance(session: Session, *, company_id, task_id) -> WorkerJob | None:
    key = f"advance:{task_id}"
    existing = session.scalar(
        select(WorkerJob).where(
            WorkerJob.idempotency_key == key,
            WorkerJob.status.in_(("pending", "leased")),
        )
    )
    if existing is not None:
        return existing
    job = WorkerJob(
        id=uuid.uuid4(),
        company_id=company_id,
        task_id=task_id,
        kind="advance_task",
        status="pending",
        attempts=0,
        next_run_at=clock.now(),
        idempotency_key=key,
        payload_json={},
        created_at=clock.now(),
    )
    session.add(job)
    return job


def kick() -> None:
    if not settings.eager_worker:
        return
    from app.worker import drain

    drain()
