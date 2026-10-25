import os
import threading
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session
from app.ingest.runner import plan_job, run_job
from app.ingest.samples import SAMPLE_DOCUMENTS
from app.models import IngestDocument, IngestJob, IngestRecord, IngestStep
from app.temporal import utc

_guard = threading.Lock()
_running: set[str] = set()
MAX_FILES = 20
MAX_BYTES = 1_500_000


def recover(db: Session) -> None:
    jobs = db.scalars(select(IngestJob).where(IngestJob.status == "running")).all()
    for job in jobs:
        job.status = "paused"
        job.error = "The run stopped before this step finished. Resume continues from that step."
        job.updated_at = _now()
    steps = db.scalars(select(IngestStep).where(IngestStep.status == "running")).all()
    for step in steps:
        step.status = "pending"
        step.error = ""
    if jobs or steps:
        db.commit()


def create_job(db: Session, organization_id: str, available_from: datetime, files: list[tuple[str, str, bytes]]) -> IngestJob:
    if not files:
        raise ValueError("Upload at least one document.")
    if len(files) > MAX_FILES:
        raise ValueError(f"Upload at most {MAX_FILES} documents at a time.")
    moment = available_from if available_from.tzinfo else available_from.replace(tzinfo=timezone.utc)
    job = IngestJob(
        id=uuid4().hex[:16],
        organization_id=organization_id,
        status="queued",
        available_from=moment,
        error="",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(job)
    for filename, media_type, payload in files:
        if len(payload) > MAX_BYTES:
            raise ValueError(f"{filename} is larger than 1.5 MB.")
        db.add(
            IngestDocument(
                id=uuid4().hex[:16],
                job_id=job.id,
                filename=_safe_name(filename),
                media_type=media_type or "text/plain",
                payload=payload,
                text="",
                content_hash="",
            )
        )
    plan_job(db, job)
    db.commit()
    return job


def sample_files() -> list[tuple[str, str, bytes]]:
    return [(name, "text/plain", text.encode()) for name, text in SAMPLE_DOCUMENTS]


def parse_available_from(value: str) -> datetime:
    if len(value) == 10:
        return utc(value + "T00:00:00+00:00")
    return utc(value)


def launch(job_id: str) -> None:
    if os.environ.get("INGEST_INLINE") == "1":
        with session() as db:
            run_job(db, job_id)
        return
    with _guard:
        if job_id in _running:
            return
        _running.add(job_id)

    def target() -> None:
        try:
            with session() as db:
                run_job(db, job_id)
        finally:
            with _guard:
                _running.discard(job_id)

    threading.Thread(target=target, name=f"ingest-{job_id}", daemon=True).start()


def change_status(db: Session, job_id: str, status: str) -> IngestJob:
    job = db.get(IngestJob, job_id)
    if job is None:
        raise ValueError("Unknown ingestion")
    if job.status == "completed":
        raise ValueError("This ingestion already finished.")
    if status == "cancelled":
        job.status = "cancelled"
    elif status == "paused":
        if job.status == "cancelled":
            raise ValueError("A cancelled ingestion cannot be paused.")
        job.status = "paused"
    elif status == "running":
        if job.status == "cancelled":
            raise ValueError("A cancelled ingestion cannot be resumed.")
        failed = db.scalars(select(IngestStep).where(IngestStep.job_id == job.id, IngestStep.status == "failed")).all()
        for step in failed:
            step.status = "pending"
            step.error = ""
        job.status = "running"
        job.error = ""
    else:
        raise ValueError("Unknown status")
    job.updated_at = _now()
    db.commit()
    if status == "running":
        launch(job.id)
    return job


def job_payload(db: Session, job: IngestJob) -> dict:
    steps = db.scalars(select(IngestStep).where(IngestStep.job_id == job.id).order_by(IngestStep.sequence, IngestStep.id)).all()
    records = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id).order_by(IngestRecord.external_id)).all()
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "available_from": job.available_from.isoformat(),
        "steps": [
            {
                "id": step.id,
                "name": step.name,
                "label": (step.detail_json or {}).get("label") or step.name,
                "status": step.status,
                "error": step.error,
                "detail": step.detail_json,
            }
            for step in steps
        ],
        "transactions": [
            {
                "id": record.external_id,
                "kind": record.kind,
                "vendor": record.vendor,
                "amount": f"{record.amount:.2f}" if record.amount is not None else None,
                "entity_ref": record.entity_ref,
                "available_from": record.available_from.isoformat(),
                "episode_id": record.episode_id,
            }
            for record in records
        ],
    }


def _safe_name(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip() or "document.txt"
    return cleaned[:300]


def _now() -> datetime:
    return datetime.now(timezone.utc)
