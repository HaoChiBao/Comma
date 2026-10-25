from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Actor, Fact, GraphEdge
from app.relationships import find_relationships, load_records, merge_edges
from app.snapshots import AccessError, get_actor, get_case, guarded_version, load_version, require_snapshot
from app.temporal import interval_contains, money

KIND_NODE = {
    "purchase_order": "PURCHASE_ORDER",
    "email": "EMAIL",
    "invoice": "INVOICE",
    "journal": "DECISION",
    "policy": "POLICY_VERSION",
    "contract": "CONTRACT",
    "memo": "DOCUMENT",
}


def record_label(version) -> tuple[str, str, str]:
    """Shown name, title, and document id.

    A stored display name is the label. When it is missing, the label is the id.
    Seeded records without a display name keep their ids.
    """
    external_id = version.artifact.external_id
    name = str((version.metadata_json or {}).get("display_name") or "").strip()
    if name:
        return name, name, external_id
    return external_id, version.artifact.title or external_id, external_id


def _case_ids(version) -> list[str]:
    return list((version.metadata_json or {}).get("case_ids") or [])


def project_graph(db: Session, snapshot_id: str, view: str) -> dict:
    snapshot = require_snapshot(db, snapshot_id)
    actor = get_actor(db, snapshot.actor_id)
    if view not in {"agent", "auditor"}:
        raise AccessError("NOT_IN_SNAPSHOT")
    if view == "auditor" and actor.role != "auditor":
        raise AccessError("INSUFFICIENT_PERMISSION")
    case = get_case(db, snapshot.case_id)
    allowed = set(snapshot.allowed_manifest_json)
    nodes = []
    edges = []
    visible_versions = []
    for version_id in sorted(allowed):
        version = load_version(db, version_id)
        if version is None or case.id not in _case_ids(version):
            continue
        visible_versions.append(version)
        amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
        formula = (version.metadata_json or {}).get("formula") or ""
        origin = "calculated" if formula else "unexplained" if version.artifact.kind == "journal" else "sourced"
        label, title, document_id = record_label(version)
        nodes.append(
            {
                "id": f"artifact:{version.id}",
                "type": KIND_NODE.get(version.artifact.kind, "DOCUMENT"),
                "label": label,
                "title": title,
                "document_id": document_id,
                "amount": money(amount),
                "origin": origin,
                "formula": formula,
                "version_id": version.id,
                "available_from": version.available_from.isoformat() if version.available_from else None,
                "document_date": version.document_date.isoformat(),
                "redacted": False,
            }
        )
        for fact in version.facts:
            if fact.fact_type not in {"unit_price", "quantity"}:
                continue
            nodes.append(
                {
                    "id": f"fact:{fact.id}",
                    "type": "CALCULATION" if fact.fact_type in {"unit_price", "quantity"} else "AMOUNT",
                    "label": fact.fact_type,
                    "title": fact.value_text,
                    "amount": money(fact.amount_decimal),
                    "origin": "calculated" if (version.metadata_json or {}).get("formula") else "sourced",
                    "version_id": version.id,
                    "available_from": version.available_from.isoformat() if version.available_from else None,
                    "document_date": version.document_date.isoformat(),
                    "redacted": False,
                }
            )
    if view == "auditor":
        for version_id in case.probe_version_ids:
            if version_id in allowed:
                continue
            version = load_version(db, version_id)
            if version is None or case.id not in _case_ids(version):
                continue
            nodes.append(
                {
                    "id": f"redacted:{version.id}",
                    "type": "REDACTED",
                    "label": "Blocked later evidence",
                    "title": "Blocked later evidence",
                    "amount": None,
                    "origin": "unavailable",
                    "version_id": version.id,
                    "available_from": version.available_from.isoformat() if version.available_from else None,
                    "document_date": None,
                    "redacted": True,
                    "reason": "FUTURE_INFORMATION" if version.availability_status == "verified" else "UNKNOWN_AVAILABILITY",
                }
            )
    node_ids = {node["id"] for node in nodes}
    for edge in db.scalars(select(GraphEdge)).all():
        if edge.id.startswith("ge-"):
            continue
        if edge.from_node_id in node_ids and edge.to_node_id in node_ids:
            edges.append(_edge(edge))
    merge_edges(edges, find_relationships(load_records(db, visible_versions)))
    for version in visible_versions:
        if version.artifact.kind != "policy":
            continue
        if not interval_contains(version.business_valid_from, version.business_valid_to, case.decision_at):
            continue
        decision = f"artifact:{case.decision_version_id}"
        policy = f"artifact:{version.id}"
        if decision in node_ids and policy in node_ids:
            edges.append(
                {
                    "id": f"gov-{version.id}",
                    "source": policy,
                    "target": decision,
                    "type": "GOVERNED_BY",
                    "inferred": False,
                    "explanation": "This policy version was business-effective at the decision and available in the snapshot.",
                    "confidence": "high",
                }
            )
    amounts = [node for node in nodes if node.get("amount") and node["type"] not in {"CALCULATION", "AMOUNT", "REDACTED"}]
    for left in amounts:
        for right in amounts:
            if left["id"] >= right["id"] or left["version_id"] == right["version_id"]:
                continue
            left_version = next(item for item in visible_versions if item.id == left["version_id"])
            right_version = next(item for item in visible_versions if item.id == right["version_id"])
            if (left_version.metadata_json or {}).get("entity_ref") != (right_version.metadata_json or {}).get("entity_ref"):
                continue
            if not (left_version.metadata_json or {}).get("entity_ref"):
                continue
            if left["amount"] == right["amount"]:
                continue
            edges.append(
                {
                    "id": f"contra-{left['id']}-{right['id']}",
                    "source": left["id"],
                    "target": right["id"],
                    "type": "CONTRADICTS",
                    "inferred": True,
                    "explanation": "These visible amounts refer to the same vendor and do not match. No formula connects them.",
                    "confidence": "medium",
                }
            )
    return {"snapshot_id": snapshot.id, "view": view, "nodes": nodes, "edges": edges}


def _edge(edge: GraphEdge) -> dict:
    return {
        "id": edge.id,
        "source": edge.from_node_id,
        "target": edge.to_node_id,
        "type": edge.edge_type,
        "inferred": edge.is_inferred,
        "explanation": edge.explanation,
        "confidence": edge.confidence,
    }


def document_payload(version) -> dict:
    amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
    label, title, _document_id = record_label(version)
    return {
        "source_version_id": version.id,
        "title": title,
        "kind": version.artifact.kind,
        "text_excerpt": version.content_text[:500],
        "document_date": version.document_date.isoformat(),
        "available_from": version.available_from.isoformat() if version.available_from else None,
        "source_locator": next((fact.source_locator for fact in version.facts), ""),
        "confidence": "seeded",
        "citation_label": label,
        "amount": money(amount),
        "origin": "unexplained" if version.artifact.kind == "journal" else "sourced",
    }


def search_allowed(db: Session, snapshot, query: str, limit: int) -> list[dict]:
    needle = query.lower()
    found = []
    for version_id in snapshot.allowed_manifest_json:
        version = load_version(db, version_id)
        if version is None:
            continue
        haystack = " ".join([version.content_text, version.artifact.title, version.artifact.external_id]).lower()
        if needle in haystack:
            found.append(document_payload(version))
        if len(found) >= limit:
            break
    return found
