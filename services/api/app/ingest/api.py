from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import db_session
from app.ingest.service import change_status, create_job, job_payload, launch, parse_available_from, sample_files
from app.models import IngestJob, Organization

router = APIRouter(prefix="/api/v1")


@router.post("/ingestions/sample")
def ingest_sample(available_from: str = "2025-09-30", organization_id: str = "org-acme", db: Session = Depends(db_session)):
    job = _start(db, organization_id, available_from, sample_files())
    return job_payload(db, job)


@router.post("/ingestions/decagon")
def ingest_decagon(db: Session = Depends(db_session)):
    from app.ingest.decagon import start_decagon

    try:
        job = start_decagon(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(job)
    return job_payload(db, job)


@router.post("/ingestions")
async def ingest_files(
    available_from: str = Form(...),
    organization_id: str = Form("org-acme"),
    files: list[UploadFile] = File(...),
    db: Session = Depends(db_session),
):
    payload = []
    for upload in files:
        payload.append((upload.filename or "document.txt", upload.content_type or "text/plain", await upload.read()))
    job = _start(db, organization_id, available_from, payload)
    return job_payload(db, job)


@router.get("/ingestions/{job_id}")
def get_ingestion(job_id: str, db: Session = Depends(db_session)):
    return job_payload(db, _job(db, job_id))


@router.post("/ingestions/{job_id}/pause")
def pause_ingestion(job_id: str, db: Session = Depends(db_session)):
    return _control(db, job_id, "paused")


@router.post("/ingestions/{job_id}/resume")
def resume_ingestion(job_id: str, db: Session = Depends(db_session)):
    return _control(db, job_id, "running")


@router.post("/ingestions/{job_id}/cancel")
def cancel_ingestion(job_id: str, db: Session = Depends(db_session)):
    return _control(db, job_id, "cancelled")


def _start(db: Session, organization_id: str, available_from: str, files: list[tuple[str, str, bytes]]) -> IngestJob:
    if db.get(Organization, organization_id) is None:
        raise HTTPException(status_code=404, detail="Unknown organization")
    try:
        moment = parse_available_from(available_from)
        job = create_job(db, organization_id, moment, files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    launch(job.id)
    db.refresh(job)
    return job


def _control(db: Session, job_id: str, status: str) -> dict:
    _job(db, job_id)
    try:
        job = change_status(db, job_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(job)
    return job_payload(db, job)


def _job(db: Session, job_id: str) -> IngestJob:
    job = db.scalars(select(IngestJob).where(IngestJob.id == job_id)).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown ingestion")
    return job
