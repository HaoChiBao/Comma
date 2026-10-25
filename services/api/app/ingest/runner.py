from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.graphiti_client import add_episode
from app.ingest.parse import extract_text, parse_document
from app.models import (
    AccessGrant,
    ArtifactVersion,
    Case,
    Fact,
    GraphEdge,
    IngestDocument,
    IngestJob,
    IngestRecord,
    IngestStep,
    SourceArtifact,
)
from app.temporal import money, sha256_text

LINK = 1000
CASES = 2000
RECONCILE = 3000


def now() -> datetime:
    return datetime.now(timezone.utc)


def plan_job(db: Session, job: IngestJob) -> None:
    documents = db.scalars(select(IngestDocument).where(IngestDocument.job_id == job.id).order_by(IngestDocument.filename)).all()
    sequence = 1
    for document in documents:
        _step(db, job.id, sequence, "read", document_id=document.id, label=document.filename)
        sequence += 1
        _step(db, job.id, sequence, "parse", document_id=document.id, label=document.filename)
        sequence += 1
    _step(db, job.id, LINK, "link", label="Link transactions, amounts, and decisions")
    _step(db, job.id, CASES, "cases", label="Open a case for each recorded decision")
    _step(db, job.id, RECONCILE, "reconcile", label="Confirm every transaction was recorded")


def run_job(db: Session, job_id: str, hook=None) -> str:
    job = db.get(IngestJob, job_id)
    if job is None:
        raise ValueError("Unknown ingestion")
    if job.status in {"paused", "cancelled", "completed"}:
        return job.status
    if job.status in {"queued", "failed"}:
        job.status = "running"
        job.error = ""
        db.commit()
    while True:
        db.refresh(job)
        if job.status in {"paused", "cancelled"}:
            return job.status
        step = _next_step(db, job_id)
        if step is None:
            job.status = "completed"
            job.updated_at = now()
            db.commit()
            return "completed"
        if hook is not None:
            hook(db, step)
            db.refresh(job)
            if job.status in {"paused", "cancelled"}:
                return job.status
        step.status = "running"
        step.started_at = now()
        step.error = ""
        job.updated_at = now()
        db.commit()
        try:
            label = (step.detail_json or {}).get("label") or step.name
            step.detail_json = {**_HANDLERS[step.name](db, job, step), "label": label}
            step.status = "completed"
            step.finished_at = now()
            db.commit()
        except Exception as exc:
            db.rollback()
            step = db.get(IngestStep, step.id)
            job = db.get(IngestJob, job_id)
            step.status = "failed"
            step.error = str(exc)[:800]
            step.finished_at = now()
            job.status = "failed"
            job.error = f"{step.name} stopped: {step.error}"
            job.updated_at = now()
            db.commit()
            return "failed"


def _next_step(db: Session, job_id: str) -> IngestStep | None:
    steps = db.scalars(select(IngestStep).where(IngestStep.job_id == job_id).order_by(IngestStep.sequence, IngestStep.id)).all()
    for step in steps:
        if step.status in {"pending", "failed"}:
            return step
    return None


def _read(db: Session, job: IngestJob, step: IngestStep) -> dict:
    document = db.get(IngestDocument, step.document_id)
    document.text = extract_text(document.filename, document.media_type, document.payload)
    document.content_hash = sha256_text(document.text)
    if not document.text:
        raise ValueError(f"{document.filename} has no readable text.")
    return {"filename": document.filename, "characters": len(document.text)}


def _parse(db: Session, job: IngestJob, step: IngestStep) -> dict:
    document = db.get(IngestDocument, step.document_id)
    parsed = parse_document(document.text, job.available_from)
    existing = db.scalars(select(IngestRecord).where(IngestRecord.document_id == document.id)).all()
    for record in existing:
        db.delete(record)
    db.flush()
    created = []
    record_seq = _max_sequence(db, job.id, "record", 1100)
    episode_seq = _max_sequence(db, job.id, "episode", 1600)
    for item in parsed:
        record = IngestRecord(
            id=uuid4().hex[:16],
            job_id=job.id,
            document_id=document.id,
            external_id=item["external_id"][:80],
            kind=item["kind"],
            vendor=item["vendor"][:200],
            entity_ref="",
            references_json=item["references"],
            amount=item["amount"],
            currency="USD",
            document_date=item["document_date"],
            available_from=item["available_from"],
            text=item["text"],
            formula=item["formula"],
            unit_price=item["unit_price"],
            quantity=item["quantity"],
        )
        db.add(record)
        created.append(record)
        record_seq += 1
        episode_seq += 1
        _step(db, job.id, record_seq, "record", document_id=document.id, record=record, label=record.external_id)
        _step(db, job.id, episode_seq, "episode", document_id=document.id, record=record, label=record.external_id)
    if not created:
        raise ValueError(f"No transactions found in {document.filename}.")
    return {"transactions": [record.external_id for record in created]}


def _link(db: Session, job: IngestJob, step: IngestStep) -> dict:
    records = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()
    clusters: dict[str, list[IngestRecord]] = {}
    for record in records:
        purchase_orders = [ref.lower() for ref in record.references_json if ref.upper().startswith("PO-")]
        key = purchase_orders[0] if purchase_orders else (f"vendor:{(record.vendor or record.external_id).lower()}")
        record.entity_ref = key[:80]
        clusters.setdefault(key, []).append(record)
    links = []
    for key, group in clusters.items():
        links.append({"entity_ref": key, "documents": [record.external_id for record in group], "amounts": [money(record.amount) for record in group]})
    return {"clusters": links}


def _record(db: Session, job: IngestJob, step: IngestStep) -> dict:
    record = db.get(IngestRecord, step.record_id)
    if record.version_id:
        return {"version_id": record.version_id, "skipped": True}
    version_id = f"u{job.id[:8]}{record.id[:8]}"
    artifact_id = f"a{job.id[:8]}{record.id[:8]}"
    artifact = SourceArtifact(
        id=artifact_id,
        organization_id=job.organization_id,
        kind=record.kind if record.kind in {"purchase_order", "email", "invoice", "journal", "policy", "contract", "memo"} else "memo",
        source_system="upload",
        external_id=record.external_id,
        title=f"{record.kind} {record.external_id}",
    )
    db.add(artifact)
    db.flush()
    db.add(
        ArtifactVersion(
            id=version_id,
            artifact_id=artifact_id,
            version=1,
            content_text=record.text,
            content_hash=sha256_text(record.text),
            document_date=record.document_date or record.available_from,
            business_valid_from=record.document_date,
            business_valid_to=None,
            available_from=record.available_from,
            available_to=None,
            ingested_at=now(),
            availability_status="verified",
            availability_evidence="Parsed from an uploaded document. A received date overrides the batch availability.",
            metadata_json={"case_ids": [], "entity_ref": record.entity_ref, "formula": record.formula, "job_id": job.id},
        )
    )
    db.flush()
    for role in ("accountant", "auditor"):
        db.add(
            AccessGrant(
                id=f"g{version_id}{role[:3]}",
                artifact_version_id=version_id,
                role=role,
                grant_from=record.available_from,
                grant_to=None,
                permission="read",
            )
        )
    if record.amount is not None:
        db.add(
            Fact(
                id=f"f{version_id}",
                artifact_version_id=version_id,
                fact_type="amount",
                entity_ref=record.entity_ref,
                amount_decimal=record.amount,
                currency=record.currency,
                value_text=money(record.amount) or "",
                source_locator=record.external_id,
                extraction_status="extracted",
            )
        )
    if record.formula and record.unit_price is not None and record.quantity is not None:
        db.add(_fact(version_id, "unit", "unit_price", record.entity_ref, record.unit_price))
        db.add(_fact(version_id, "qty", "quantity", record.entity_ref, record.quantity))
        db.add(
            GraphEdge(
                id=f"e{version_id}unit",
                from_node_id=f"fact:f{version_id}unit",
                to_node_id=f"artifact:{version_id}",
                edge_type="DERIVED_FROM",
                supporting_version_id=version_id,
                confidence="high",
                is_inferred=False,
                explanation=record.formula,
                created_at=now(),
            )
        )
    record.version_id = version_id
    return {"version_id": version_id, "amount": money(record.amount), "available_from": record.available_from.isoformat()}


def _episode(db: Session, job: IngestJob, step: IngestStep) -> dict:
    record = db.get(IngestRecord, step.record_id)
    if record.episode_id and not _episode_needs_graphiti(db, record):
        return {"episode_id": record.episode_id, "skipped": True}
    result = add_episode(db, record, job.id, job.organization_id.replace("-", "_"))
    record.episode_id = result["episode_id"]
    return result


def _episode_needs_graphiti(db: Session, record: IngestRecord) -> bool:
    import os

    from app.models import GraphEpisode

    if not os.environ.get("GRAPHITI_URI"):
        return False
    episode = db.get(GraphEpisode, record.episode_id)
    return episode is None or episode.backend != "graphiti"


def _cases(db: Session, job: IngestJob, step: IngestStep) -> dict:
    records = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()
    groups: dict[str, list[IngestRecord]] = {}
    for record in records:
        groups.setdefault(record.entity_ref or record.id, []).append(record)
    opened = []
    for entity, group in groups.items():
        decision = next((record for record in group if record.kind == "journal"), group[0])
        case_id = f"ING-{decision.id[:8]}"
        if db.get(Case, case_id) is None:
            db.add(
                Case(
                    id=case_id,
                    organization_id=job.organization_id,
                    case_type="uploaded_decision",
                    decision_artifact_id=("a" + decision.version_id[1:]) if decision.version_id else decision.id,
                    decision_version_id=decision.version_id,
                    decision_at=decision.document_date or decision.available_from,
                    summary=_summary(group, decision),
                    status="open",
                    vendor_name=decision.vendor or "Uploaded documents",
                    entity_ref=entity,
                    recorded_amount=decision.amount or Decimal("0"),
                    probe_version_ids=[record.version_id for record in group if record.version_id],
                )
            )
        for record in group:
            version = db.get(ArtifactVersion, record.version_id)
            if version is None:
                continue
            metadata = dict(version.metadata_json or {})
            metadata["case_ids"] = sorted(set(metadata.get("case_ids") or []) | {case_id})
            version.metadata_json = metadata
        opened.append(case_id)
    return {"cases": opened}


def _reconcile(db: Session, job: IngestJob, step: IngestStep) -> dict:
    records = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()
    missing = [record.external_id for record in records if not record.version_id or not record.episode_id]
    if missing:
        raise ValueError("These transactions were parsed but not finished: " + ", ".join(missing))
    amounts = sorted({money(record.amount) for record in records if record.amount is not None})
    return {"transactions": len(records), "amounts": amounts}


def _summary(group: list[IngestRecord], decision: IngestRecord) -> str:
    others = [record.external_id for record in group if record.id != decision.id]
    extra = f" Linked documents: {', '.join(others)}." if others else ""
    return (
        f"{decision.vendor or 'An uploaded document'} recorded {decision.external_id} "
        f"at {money(decision.amount) or 'an unknown amount'}.{extra} "
        "Amounts appear on the graph only after each document was available."
    )


def _fact(version_id: str, suffix: str, fact_type: str, entity: str, amount: Decimal) -> Fact:
    return Fact(
        id=f"f{version_id}{suffix}",
        artifact_version_id=version_id,
        fact_type=fact_type,
        entity_ref=entity,
        amount_decimal=amount,
        currency="USD",
        value_text=money(amount) or "",
        source_locator=fact_type,
        extraction_status="extracted",
    )


def _max_sequence(db: Session, job_id: str, name: str, floor: int) -> int:
    current = [step.sequence for step in db.scalars(select(IngestStep).where(IngestStep.job_id == job_id, IngestStep.name == name)).all()]
    return max(current, default=floor)


def _step(db: Session, job_id: str, sequence: int, name: str, document_id: str | None = None, record: IngestRecord | None = None, label: str = "") -> None:
    db.add(
        IngestStep(
            id=uuid4().hex[:16],
            job_id=job_id,
            document_id=document_id,
            record_id=record.id if record else None,
            sequence=sequence,
            name=name,
            status="pending",
            detail_json={"label": label or name},
            error="",
        )
    )


_HANDLERS = {
    "read": _read,
    "parse": _parse,
    "link": _link,
    "record": _record,
    "episode": _episode,
    "cases": _cases,
    "reconcile": _reconcile,
}

from app.ingest.decagon import HANDLERS as DECAGON_HANDLERS

_HANDLERS.update(DECAGON_HANDLERS)
