from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.graph import KIND_NODE, record_label
from app.models import Actor, ArtifactVersion, Case, Claim, GraphEdge, IngestJob, IngestStep, SourceArtifact
from app.graphstore.claims import build_claims
from app.graphstore.store import edges_from_claims, episode_edges, index_counts, structure_graph
from app.relationships import find_relationships, load_records, merge_edges
from app.snapshots import AccessError, get_actor
from app.temporal import exclusion_reason, money

ORG = "org-decagon"


def company_graph(db: Session, organization_id: str, actor_id: str, as_of: datetime, mode: str, view: str | None) -> dict:
    if mode not in {"audit", "pre_decision"}:
        raise AccessError("NOT_IN_SNAPSHOT")
    actor = get_actor(db, actor_id)
    if actor.organization_id != organization_id:
        raise AccessError("OUTSIDE_ORGANIZATION")
    if view is None:
        view = "auditor" if actor.role == "auditor" else "agent"
    if view == "auditor" and actor.role != "auditor":
        raise AccessError("INSUFFICIENT_PERMISSION")
    cases = list(db.scalars(select(Case).where(Case.organization_id == organization_id).order_by(Case.id)).all())
    hidden = {case.decision_version_id for case in cases} if mode == "pre_decision" else set()
    versions = list(
        db.scalars(
            select(ArtifactVersion)
            .join(SourceArtifact)
            .where(SourceArtifact.organization_id == organization_id)
            .options(
                selectinload(ArtifactVersion.artifact),
                selectinload(ArtifactVersion.grants),
                selectinload(ArtifactVersion.facts),
            )
        ).all()
    )
    nodes = []
    documents = []
    linked = 0
    for version in versions:
        case_ids = list((version.metadata_json or {}).get("case_ids") or [])
        on_graph = bool(case_ids) or version.artifact.kind == "policy"
        reason = exclusion_reason(
            version,
            version.grants,
            organization_id=organization_id,
            role=actor.role,
            as_of=as_of,
            mode=mode,
            hidden_version_ids=hidden,
        )
        if reason is None:
            documents.append(_document(version, case_ids, on_graph))
            nodes.append(_node(version, case_ids, redacted=False))
            if on_graph:
                linked += 1
        elif on_graph and view == "auditor" and reason in {"FUTURE_INFORMATION", "INSUFFICIENT_PERMISSION", "NOT_IN_SNAPSHOT"}:
            linked += 1
            nodes.append(_node(version, case_ids, redacted=True))
    documents.sort(key=lambda item: (item["available_from"] or "", item["id"]))
    node_ids = {node["id"] for node in nodes}
    edges = []
    for edge in db.scalars(select(GraphEdge)).all():
        if edge.id.startswith("ge-"):
            continue
        if edge.from_node_id in node_ids and edge.to_node_id in node_ids:
            edges.append(
                {
                    "id": edge.id,
                    "source": edge.from_node_id,
                    "target": edge.to_node_id,
                    "type": edge.edge_type,
                    "inferred": edge.is_inferred,
                    "explanation": edge.explanation,
                    "confidence": edge.confidence,
                }
            )
    redacted = {node["id"] for node in nodes if node["redacted"]}
    visible = [version for version in versions if f"artifact:{version.id}" in node_ids and f"artifact:{version.id}" not in redacted]
    records = load_records(db, visible)
    merge_edges(edges, find_relationships(records))
    visible_ids = {version.id for version in visible}
    stored = list(db.scalars(select(Claim).where(Claim.organization_id == organization_id)).all())
    merge_edges(edges, edges_from_claims(stored or build_claims(records), records, as_of, visible_ids))
    merge_edges(edges, episode_edges(db, records, {record["external_id"] for record in records}))
    extra_nodes, extra_edges = structure_graph(db, organization_id, records)
    present = {node["id"] for node in nodes}
    for extra in extra_nodes:
        if extra["id"] not in present:
            nodes.append(extra)
            present.add(extra["id"])
    merge_edges(edges, extra_edges)
    return {
        "organization_id": organization_id,
        "as_of": as_of.isoformat(),
        "mode": mode,
        "view": view,
        "actor": {"id": actor.id, "name": actor.name, "role": actor.role},
        "actors": [
            {"id": person.id, "name": person.name, "role": person.role}
            for person in db.scalars(select(Actor).where(Actor.organization_id == organization_id).order_by(Actor.name)).all()
        ],
        "nodes": nodes,
        "edges": edges,
        "documents": documents,
        "cases": [
            {
                "id": case.id,
                "title": case.summary.split(".")[0],
                "vendor": case.vendor_name,
                "finding": _finding(case.id, nodes, view),
            }
            for case in cases
        ],
        "corpus": {"documents": len(versions), "visible": len(documents), "on_graph": linked},
        "index": index_counts(db, organization_id),
        "run": _run(db, organization_id),
    }


def preview_document(db: Session, organization_id: str, actor_id: str, as_of: datetime, mode: str, version_id: str) -> dict:
    """One visible document, including original bytes metadata when the file is still stored.

    A hidden version raises before any title, amount, text, or bytes are returned.
    ``text`` is the stored extraction, never the short graph excerpt.
    """
    from app.document_preview import preview_fields

    version = _visible_version(db, organization_id, actor_id, as_of, mode, version_id)
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    body = {
        "id": version.artifact.external_id,
        "version_id": version.id,
        "title": version.artifact.title,
        "kind": version.artifact.kind,
        "amount": money(amount),
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "document_date": version.document_date.isoformat() if version.document_date else None,
        "text": version.content_text,
    }
    body.update(preview_fields(db, version))
    return body


def document_original(db: Session, organization_id: str, actor_id: str, as_of: datetime, mode: str, version_id: str) -> tuple[bytes, str, str] | None:
    """Bytes for a visible version, or None when only extracted text was kept."""
    from app.document_preview import locate_original

    version = _visible_version(db, organization_id, actor_id, as_of, mode, version_id)
    found = locate_original(db, version)
    if found is None:
        return None
    return found.data, found.media_type, found.filename


def _visible_version(db: Session, organization_id: str, actor_id: str, as_of: datetime, mode: str, version_id: str) -> ArtifactVersion:
    if mode not in {"audit", "pre_decision"}:
        raise AccessError("NOT_IN_SNAPSHOT")
    actor = get_actor(db, actor_id)
    if actor.organization_id != organization_id:
        raise AccessError("OUTSIDE_ORGANIZATION")
    version = db.scalar(
        select(ArtifactVersion)
        .join(SourceArtifact)
        .where(ArtifactVersion.id == version_id, SourceArtifact.organization_id == organization_id)
        .options(selectinload(ArtifactVersion.artifact), selectinload(ArtifactVersion.grants), selectinload(ArtifactVersion.facts))
    )
    if version is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    hidden = set()
    if mode == "pre_decision":
        hidden = set(
            db.scalars(select(Case.decision_version_id).where(Case.organization_id == organization_id, Case.decision_version_id.is_not(None))).all()
        )
    reason = exclusion_reason(
        version,
        version.grants,
        organization_id=organization_id,
        role=actor.role,
        as_of=as_of,
        mode=mode,
        hidden_version_ids=hidden,
    )
    if reason:
        raise AccessError(reason)
    return version


def _document(version: ArtifactVersion, case_ids: list[str], on_graph: bool) -> dict:
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    _label, title, external_id = record_label(version)
    return {
        "id": external_id,
        "version_id": version.id,
        "kind": version.artifact.kind,
        "title": title,
        "amount": money(amount),
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "case_id": case_ids[0] if case_ids else "",
        "on_graph": on_graph,
    }


def _node(version: ArtifactVersion, case_ids: list[str], *, redacted: bool) -> dict:
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    kind = version.artifact.kind
    if kind == "policy":
        group = "policy"
    elif case_ids:
        group = case_ids[0]
    else:
        group = kind or "other"
    include_text = bool(case_ids) or kind == "policy"
    if redacted:
        label, title, document_id = "Blocked evidence", "Blocked evidence", ""
    else:
        label, title, document_id = record_label(version)
    return {
        "id": f"artifact:{version.id}",
        "type": "REDACTED" if redacted else KIND_NODE.get(kind, "DOCUMENT"),
        "label": label,
        "title": title,
        "document_id": document_id,
        "amount": None if redacted else money(amount),
        "group": group,
        "case_id": case_ids[0] if case_ids else "",
        "origin": "calculated" if (version.metadata_json or {}).get("formula") else "unexplained" if kind == "journal" else "sourced",
        "formula": (version.metadata_json or {}).get("formula") or "",
        "version_id": version.id,
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "document_date": None if redacted else version.document_date.isoformat(),
        "redacted": redacted,
        "text": "" if redacted or not include_text else version.content_text[:360],
    }


def _finding(case_id: str, nodes: list[dict], view: str) -> str:
    mine = [node for node in nodes if node["case_id"] == case_id]
    visible = [node for node in mine if not node["redacted"] and node.get("amount")]
    journals = [node for node in visible if node["type"] == "DECISION"]
    rest = [node for node in visible if node["type"] not in {"DECISION", "POLICY"}]
    parts = []
    if journals:
        parts.append(f"Recorded {journals[0]['label']} at ${journals[0]['amount']}.")
    elif any(node["redacted"] and node["origin"] == "unexplained" for node in mine):
        parts.append("The recorded decision is outside this snapshot.")
    else:
        parts.append("No recorded decision is inside this snapshot.")
    invoices = [node for node in rest if node["type"] == "INVOICE"]
    if journals and invoices:
        delta = Decimal(invoices[0]["amount"]) - Decimal(journals[0]["amount"])
        if delta == 0:
            parts.append(f"{invoices[0]['label']} matches at ${invoices[0]['amount']}.")
        else:
            direction = "higher" if delta > 0 else "lower"
            parts.append(f"{invoices[0]['label']} is ${invoices[0]['amount']}, ${abs(delta):.2f} {direction}.")
    elif len({node["amount"] for node in visible}) > 1:
        parts.append("The amounts in this snapshot do not agree.")
    if view == "auditor" and any(node["redacted"] for node in mine):
        parts.append("Some related evidence is blocked at this time.")
    return " ".join(parts)


def _run(db: Session, organization_id: str) -> dict | None:
    job = db.scalars(select(IngestJob).where(IngestJob.organization_id == organization_id).order_by(IngestJob.created_at.desc())).first()
    if job is None:
        return None
    steps = list(db.scalars(select(IngestStep).where(IngestStep.job_id == job.id)).all())
    running = next((step for step in steps if step.status == "running"), None)
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "completed": sum(1 for step in steps if step.status == "completed"),
        "total": len(steps),
        "label": ((running.detail_json or {}).get("label") if running else "") or "",
    }
