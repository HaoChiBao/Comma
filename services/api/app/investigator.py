from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import append_event
from app.graph import document_payload
from app.models import Investigation, Proposal, ToolEvent
from app.relationships import find_relationships, load_records
from app.snapshots import AccessError, create_snapshot, get_case, guarded_version, require_snapshot
from app.temporal import money


def _log(db: Session, investigation_id: str, tool_name: str, params: dict, result_ids: list[str], blocked: str | None) -> None:
    db.add(
        ToolEvent(
            id=f"tool_{uuid4().hex[:16]}",
            investigation_id=investigation_id,
            tool_name=tool_name,
            params_json=params,
            result_ids_json=result_ids,
            blocked_reason=blocked,
            timestamp=datetime.now(timezone.utc),
        )
    )


def run_investigation(db: Session, snapshot_id: str) -> Investigation:
    snapshot = require_snapshot(db, snapshot_id)
    case = get_case(db, snapshot.case_id)
    investigation = Investigation(
        id=f"inv_{uuid4().hex[:16]}",
        case_id=case.id,
        snapshot_id=snapshot.id,
        agent_version="deterministic-1",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        finding_json=None,
        status="running",
    )
    db.add(investigation)
    db.flush()
    observations = []
    read_ids = []
    readable = []
    blocked = 0
    for version_id in case.probe_version_ids:
        try:
            version = guarded_version(db, snapshot, version_id)
        except AccessError as error:
            blocked += 1
            _log(db, investigation.id, "get_document", {"source_version_id": version_id}, [], error.code)
            continue
        payload = document_payload(version)
        readable.append(version)
        read_ids.append(version.id)
        _log(db, investigation.id, "get_document", {"source_version_id": version.id}, [version.id], None)
        if payload["amount"] is not None:
            observations.append(
                {
                    "text": f"{payload['citation_label']} indicated {payload['amount']}",
                    "source_version_id": version.id,
                    "amount": payload["amount"],
                    "kind": payload["kind"],
                    "citation_label": payload["citation_label"],
                }
            )
    outcome = next((item for item in observations if item["kind"] == "journal"), None)
    estimates = [item for item in observations if item["kind"] != "journal" and item["kind"] != "policy"]
    distinct = {item["amount"] for item in estimates}
    if outcome is None and not estimates:
        finding = "INSUFFICIENT_EVIDENCE"
    elif outcome is None and len(distinct) > 1:
        finding = "QUESTIONABLE"
    elif outcome is None:
        finding = "SUPPORTED"
    elif not estimates:
        finding = "INSUFFICIENT_EVIDENCE"
    elif any(item["amount"] != outcome["amount"] for item in estimates):
        finding = "QUESTIONABLE"
    else:
        finding = "SUPPORTED"
    missing = []
    if blocked:
        missing.append("At least one related document was not available in this snapshot.")
    if finding == "INSUFFICIENT_EVIDENCE":
        missing.append("No preserved source explains the recorded amount.")
    uncertainties = ["Cannot prove which source a person actually used."]
    if outcome and outcome["kind"] == "journal":
        uncertainties.append("The recorded accrual has no preserved formula, so its origin is unexplained.")
    amount_text = ", ".join(f"{item['citation_label']} {item['amount']}" for item in observations) or "no amounts"
    relationships = find_relationships(load_records(db, readable))
    summary = f"Using only the snapshot, the visible amounts are {amount_text}."
    if outcome and finding == "QUESTIONABLE":
        summary = f"The recorded {outcome['amount']} differs from other amounts visible in this snapshot."
    if relationships:
        summary += " Reading the documents found " + str(len(relationships)) + " relationships."
    proposal_type = "REQUEST_DOCUMENT" if finding == "INSUFFICIENT_EVIDENCE" else "REQUEST_HUMAN_REVIEW"
    rationale = "A person should confirm the basis of the recorded amount. This agent does not post an entry."
    finding_json = {
        "case_id": case.id,
        "snapshot_id": snapshot.id,
        "as_of": snapshot.as_of.isoformat(),
        "mode": snapshot.mode,
        "finding": finding,
        "summary": summary,
        "observations": observations,
        "missing_information": missing,
        "uncertainties": uncertainties,
        "proposal": {"type": proposal_type, "rationale": rationale},
        "blocked_access_attempts": blocked,
        "unsafe": False,
        "relationships": [
            {
                "type": item["type"],
                "source": item["source_label"],
                "target": item["target_label"],
                "explanation": item["explanation"],
            }
            for item in relationships
        ],
    }
    proposal = Proposal(
        id=f"prop_{uuid4().hex[:16]}",
        investigation_id=investigation.id,
        action_type=proposal_type,
        payload_json={"evidence_ids": read_ids},
        rationale=rationale,
        status="pending",
    )
    investigation.finding_json = finding_json
    investigation.completed_at = datetime.now(timezone.utc)
    investigation.status = "completed"
    db.add(proposal)
    append_event(db, snapshot.organization_id, "investigation_completed", investigation.id, {"finding": finding, "case_id": case.id}, snapshot.actor_id)
    db.commit()
    db.refresh(investigation)
    return investigation


def unsafe_baseline(db: Session, *, case_id: str, actor_id: str, as_of: datetime, mode: str) -> dict:
    """Intentionally ignores availability. Not used for a real financial action."""
    constrained = create_snapshot(db, case_id=case_id, actor_id=actor_id, as_of=as_of, mode=mode)
    case = get_case(db, constrained.case_id)
    leaked = []
    texts = []
    for version_id in case.probe_version_ids:
        if version_id in set(constrained.allowed_manifest_json):
            continue
        from app.snapshots import load_version

        version = load_version(db, version_id)
        if version is None or version.artifact.organization_id != constrained.organization_id:
            continue
        if version.availability_status != "verified":
            continue
        payload = document_payload(version)
        leaked.append(version.id)
        if payload["amount"]:
            texts.append(f"{payload['citation_label']} {payload['amount']}")
    leak_text = ", ".join(texts) if texts else "none"
    return {
        "unsafe": True,
        "warning": "This unrestricted baseline ignores knowledge time. Do not use it for a financial action.",
        "summary": f"Unsafe baseline used later evidence: {leak_text}.",
        "leaked_version_ids": leaked,
        "as_of_ignored": True,
    }


def investigation_detail(db: Session, investigation_id: str) -> dict:
    investigation = db.get(Investigation, investigation_id)
    if investigation is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    events = list(db.scalars(select(ToolEvent).where(ToolEvent.investigation_id == investigation_id).order_by(ToolEvent.timestamp)).all())
    proposal = db.scalar(select(Proposal).where(Proposal.investigation_id == investigation_id))
    return {
        "id": investigation.id,
        "case_id": investigation.case_id,
        "snapshot_id": investigation.snapshot_id,
        "status": investigation.status,
        "finding": investigation.finding_json,
        "tool_events": [
            {
                "id": event.id,
                "tool_name": event.tool_name,
                "params": event.params_json,
                "result_ids": event.result_ids_json,
                "blocked_reason": event.blocked_reason,
                "timestamp": event.timestamp.isoformat(),
            }
            for event in events
        ],
        "proposal": None
        if proposal is None
        else {
            "id": proposal.id,
            "action_type": proposal.action_type,
            "rationale": proposal.rationale,
            "status": proposal.status,
            "payload": proposal.payload_json,
        },
    }
