from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.graph import KIND_NODE
from app.models import Actor, ArtifactVersion, Case, GraphEdge, IngestJob, IngestStep, SourceArtifact
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
            if on_graph:
                linked += 1
                nodes.append(_node(version, case_ids, redacted=False))
        elif on_graph and view == "auditor" and reason in {"FUTURE_INFORMATION", "INSUFFICIENT_PERMISSION", "NOT_IN_SNAPSHOT"}:
            linked += 1
            nodes.append(_node(version, case_ids, redacted=True))
    documents.sort(key=lambda item: (item["available_from"] or "", item["id"]))
    node_ids = {node["id"] for node in nodes}
    edges = []
    for edge in db.scalars(select(GraphEdge)).all():
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
        "run": _run(db, organization_id),
    }


def _document(version: ArtifactVersion, case_ids: list[str], on_graph: bool) -> dict:
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    return {
        "id": version.artifact.external_id,
        "version_id": version.id,
        "kind": version.artifact.kind,
        "title": version.artifact.title,
        "amount": money(amount),
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "case_id": case_ids[0] if case_ids else "",
        "on_graph": on_graph,
    }


def _node(version: ArtifactVersion, case_ids: list[str], *, redacted: bool) -> dict:
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    kind = version.artifact.kind
    group = "policy" if kind == "policy" else (case_ids[0] if case_ids else "other")
    return {
        "id": f"artifact:{version.id}",
        "type": "REDACTED" if redacted else KIND_NODE.get(kind, "DOCUMENT"),
        "label": "Blocked evidence" if redacted else version.artifact.external_id,
        "title": "Blocked evidence" if redacted else version.artifact.title,
        "amount": None if redacted else money(amount),
        "group": group,
        "case_id": case_ids[0] if case_ids else "",
        "origin": "unexplained" if kind == "journal" else "sourced",
        "version_id": version.id,
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "document_date": None if redacted else version.document_date.isoformat(),
        "redacted": redacted,
        "text": "" if redacted else version.content_text[:360],
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
    if rest:
        parts.append("Visible evidence: " + ", ".join(f"{node['label']} ${node['amount']}" for node in rest) + ".")
    if len({node["amount"] for node in rest}) > 1:
        parts.append("Those amounts do not agree.")
    invoices = [node for node in rest if node["type"] == "INVOICE"]
    if journals and invoices:
        delta = Decimal(invoices[0]["amount"]) - Decimal(journals[0]["amount"])
        direction = "higher" if delta > 0 else "lower"
        parts.append(f"The invoice is ${abs(delta):.2f} {direction} than the accrual.")
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
