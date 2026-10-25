from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import append_event
from app.db import db_session
from app.graph import document_payload, project_graph, search_allowed
from app.investigator import investigation_detail, run_investigation, unsafe_baseline
from app.models import Actor, Approval, AuditEvent, Case, Proposal, Snapshot
from app.snapshots import AccessError, create_snapshot, get_actor, get_case, guarded_version, require_snapshot
from app.temporal import money, utc

router = APIRouter(prefix="/api/v1")


class SnapshotIn(BaseModel):
    case_id: str
    actor_id: str
    as_of: datetime
    mode: str = "audit"


class InvestigationIn(BaseModel):
    snapshot_id: str


class SearchIn(BaseModel):
    query: str
    limit: int = Field(default=8, le=20)


class ReviewIn(BaseModel):
    actor_id: str
    decision: str
    comment: str = ""


class CompareIn(BaseModel):
    case_id: str
    actor_id: str
    as_of: datetime
    mode: str = "audit"


def _as_of(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("/actors")
def list_actors(db: Session = Depends(db_session)):
    actors = db.scalars(select(Actor).order_by(Actor.name)).all()
    return [{"id": actor.id, "name": actor.name, "role": actor.role, "organization_id": actor.organization_id} for actor in actors]


@router.get("/company-graph")
def company_graph_route(
    organization_id: str = "org-decagon",
    actor_id: str = "actor-decagon-finance",
    as_of: str = "2025-12-15T12:00:00+00:00",
    mode: str = "audit",
    view: str = "",
    db: Session = Depends(db_session),
):
    from app.company_graph import company_graph
    from app.ingest.decagon import ensure_company

    if organization_id == "org-decagon":
        ensure_company(db)
    return company_graph(db, organization_id, actor_id, utc(as_of), mode, view or None)


@router.get("/cases")
def list_cases(db: Session = Depends(db_session)):
    cases = db.scalars(select(Case).order_by(Case.id)).all()
    return [_case_out(case) for case in cases]


@router.get("/cases/{case_id}")
def get_case_route(case_id: str, db: Session = Depends(db_session)):
    return _case_out(get_case(db, case_id))


@router.post("/snapshots")
def post_snapshot(body: SnapshotIn, db: Session = Depends(db_session)):
    snapshot = create_snapshot(db, case_id=body.case_id, actor_id=body.actor_id, as_of=_as_of(body.as_of), mode=body.mode)
    append_event(db, snapshot.organization_id, "snapshot_created", snapshot.id, {"hash": snapshot.manifest_hash, "case_id": snapshot.case_id}, snapshot.actor_id)
    db.commit()
    return _snapshot_out(snapshot)


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, db: Session = Depends(db_session)):
    return _snapshot_out(require_snapshot(db, snapshot_id))


@router.post("/snapshots/{snapshot_id}/search")
def search_snapshot(snapshot_id: str, body: SearchIn, db: Session = Depends(db_session)):
    snapshot = require_snapshot(db, snapshot_id)
    return {"results": search_allowed(db, snapshot, body.query, body.limit)}


@router.get("/snapshots/{snapshot_id}/documents/{version_id}")
def get_document(snapshot_id: str, version_id: str, db: Session = Depends(db_session)):
    snapshot = require_snapshot(db, snapshot_id)
    version = guarded_version(db, snapshot, version_id)
    return document_payload(version)


@router.get("/snapshots/{snapshot_id}/graph")
def get_graph(snapshot_id: str, view: str = "agent", db: Session = Depends(db_session)):
    return project_graph(db, snapshot_id, view)


@router.get("/snapshots/{snapshot_id}/availability/{version_id}")
def explain_availability(snapshot_id: str, version_id: str, db: Session = Depends(db_session)):
    snapshot = require_snapshot(db, snapshot_id)
    actor = get_actor(db, snapshot.actor_id)
    if version_id in set(snapshot.allowed_manifest_json):
        version = guarded_version(db, snapshot, version_id)
        return {"status": "included", "reason": None, "available_from": version.available_from.isoformat() if version.available_from else None}
    from app.snapshots import denial_code, load_version

    reason = denial_code(db, snapshot, version_id)
    version = load_version(db, version_id)
    payload = {"status": "excluded", "reason": reason}
    if actor.role == "auditor" and version is not None:
        payload["available_from"] = version.available_from.isoformat() if version.available_from else None
    return payload


@router.post("/investigations")
def post_investigation(body: InvestigationIn, db: Session = Depends(db_session)):
    investigation = run_investigation(db, body.snapshot_id)
    return investigation_detail(db, investigation.id)


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: str, db: Session = Depends(db_session)):
    return investigation_detail(db, investigation_id)


@router.post("/proposals/{proposal_id}/review")
def review_proposal(proposal_id: str, body: ReviewIn, db: Session = Depends(db_session)):
    if body.decision not in {"approve", "reject"}:
        raise AccessError("INSUFFICIENT_PERMISSION")
    actor = get_actor(db, body.actor_id)
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    detail = investigation_detail(db, proposal.investigation_id)
    snapshot = require_snapshot(db, detail["snapshot_id"])
    if actor.organization_id != snapshot.organization_id or actor.role != "auditor":
        raise AccessError("INSUFFICIENT_PERMISSION")
    proposal.status = "approved" if body.decision == "approve" else "rejected"
    approval = Approval(
        id=f"appr_{proposal.id[-8:]}",
        proposal_id=proposal.id,
        actor_id=actor.id,
        decision=body.decision,
        comment=body.comment,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(approval)
    append_event(
        db,
        snapshot.organization_id,
        "proposal_reviewed",
        proposal.id,
        {"decision": body.decision, "case_id": snapshot.case_id},
        actor.id,
    )
    db.commit()
    return {"proposal_id": proposal.id, "status": proposal.status, "decision": body.decision}


@router.get("/audit")
def list_audit(case_id: str | None = None, db: Session = Depends(db_session)):
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.timestamp)).all()
    if case_id:
        events = [event for event in events if case_id in str(event.payload_json)]
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "subject_id": event.subject_id,
            "payload": event.payload_json,
            "actor_id": event.actor_id,
            "timestamp": event.timestamp.isoformat(),
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
        }
        for event in events
    ]


@router.post("/evaluations/compare")
def compare(body: CompareIn, db: Session = Depends(db_session)):
    snapshot = create_snapshot(db, case_id=body.case_id, actor_id=body.actor_id, as_of=_as_of(body.as_of), mode=body.mode)
    investigation = run_investigation(db, snapshot.id)
    constrained = investigation_detail(db, investigation.id)
    unsafe = unsafe_baseline(db, case_id=body.case_id, actor_id=body.actor_id, as_of=_as_of(body.as_of), mode=body.mode)
    return {"constrained": constrained, "unrestricted": unsafe, "leakage": unsafe["leaked_version_ids"]}


def _case_out(case: Case) -> dict:
    return {
        "id": case.id,
        "case_type": case.case_type,
        "summary": case.summary,
        "status": case.status,
        "vendor_name": case.vendor_name,
        "decision_at": case.decision_at.isoformat(),
        "recorded_amount": money(case.recorded_amount),
        "organization_id": case.organization_id,
    }


def _snapshot_out(snapshot: Snapshot) -> dict:
    return {
        "id": snapshot.id,
        "case_id": snapshot.case_id,
        "organization_id": snapshot.organization_id,
        "actor_id": snapshot.actor_id,
        "as_of": snapshot.as_of.isoformat(),
        "mode": snapshot.mode,
        "allowed_count": len(snapshot.allowed_manifest_json),
        "allowed_source_version_ids": snapshot.allowed_manifest_json,
        "excluded_counts_by_reason": snapshot.excluded_counts_json,
        "manifest_hash": snapshot.manifest_hash,
        "created_at": snapshot.created_at.isoformat(),
    }
