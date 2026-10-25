"""Relationships found by reading documents. Nothing in here comes from a supplied edge list."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.parse import ID_RE, _normalize_id
from app.models import IngestRecord
from app.temporal import interval_contains

_TRANSACTION = {"PO", "INV", "JE", "EM", "MSG", "CTR", "AMD", "SVC", "ACR"}


def find_relationships(records: list[dict]) -> list[dict]:
    by_id = {record["external_id"]: record for record in records}
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(source: dict, target: dict, edge_type: str, explanation: str, *, inferred: bool = True, confidence: str = "medium") -> None:
        if source["version_id"] == target["version_id"]:
            return
        key = (edge_type, frozenset((source["version_id"], target["version_id"])))
        if key in seen:
            return
        seen.add(key)
        left, right = sorted((source["external_id"], target["external_id"]))
        found.append(
            {
                "id": f"rel-{edge_type}-{left}-{right}"[:80],
                "source": f"artifact:{source['version_id']}",
                "target": f"artifact:{target['version_id']}",
                "source_label": source["external_id"],
                "target_label": target["external_id"],
                "type": edge_type,
                "inferred": inferred,
                "explanation": explanation,
                "confidence": confidence,
            }
        )

    for record in records:
        for ref in record.get("references") or []:
            other = by_id.get(ref)
            if other is None or other["external_id"] == record["external_id"]:
                continue
            add(record, other, "MENTIONS", "This document names the other record. That does not prove it was used.")

    anchors: dict[str, dict[str, dict]] = {}
    for record in records:
        refs = {record["external_id"], *(record.get("references") or [])}
        for ref in refs:
            if ref in by_id and _is_transaction(ref):
                anchors.setdefault(ref, {})[record["external_id"]] = record
    for members in anchors.values():
        _amount_pairs(
            list(members.values()),
            add,
            "These documents name the same transaction and the amounts differ.",
            "These documents name the same transaction and the amounts match.",
        )

    same_record: dict[str, dict[str, dict]] = {}
    for record in records:
        key = record.get("entity_ref") or ""
        if key:
            same_record.setdefault(key, {})[record["external_id"]] = record
    for members in same_record.values():
        _amount_pairs(
            list(members.values()),
            add,
            "These documents refer to the same record and the amounts differ.",
            "These documents refer to the same record and the amounts match.",
        )

    cases: dict[str, dict[str, dict]] = {}
    for record in records:
        for case_id in record.get("case_ids") or []:
            cases.setdefault(case_id, {})[record["external_id"]] = record
    for members in cases.values():
        group = list(members.values())
        journals = [item for item in group if item["kind"] == "journal"]
        policies = [item for item in group if item["kind"] == "policy"]
        for policy in policies:
            for journal in journals:
                moment = journal.get("available_from")
                if moment is not None and interval_contains(policy.get("business_valid_from"), policy.get("business_valid_to"), moment):
                    add(
                        policy,
                        journal,
                        "GOVERNS",
                        "This policy was in effect when the decision was recorded.",
                        inferred=False,
                        confidence="high",
                    )
    return found


def merge_edges(edges: list[dict], extra: list[dict]) -> list[dict]:
    seen = {(edge["type"], frozenset((edge["source"], edge["target"]))) for edge in edges}
    for edge in extra:
        key = (edge["type"], frozenset((edge["source"], edge["target"])))
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "id": edge["id"],
                "source": edge["source"],
                "target": edge["target"],
                "type": edge["type"],
                "inferred": edge["inferred"],
                "explanation": edge["explanation"],
                "confidence": edge["confidence"],
            }
        )
    return edges


def load_records(db: Session, versions: list) -> list[dict]:
    wanted = {version.id for version in versions}
    stored = {
        record.version_id: record
        for record in db.scalars(select(IngestRecord)).all()
        if record.version_id in wanted
    }
    records = []
    for version in versions:
        record = stored.get(version.id)
        amount = next((fact.amount_decimal for fact in version.facts if fact.fact_type == "amount"), None)
        references = list(record.references_json) if record is not None else _ids(version.content_text)
        records.append(
            {
                "external_id": version.artifact.external_id,
                "version_id": version.id,
                "kind": version.artifact.kind,
                "amount": amount if isinstance(amount, Decimal) or amount is None else Decimal(amount),
                "references": references,
                "case_ids": list((version.metadata_json or {}).get("case_ids") or []),
                "entity_ref": (version.metadata_json or {}).get("entity_ref") or "",
                "available_from": version.available_from,
                "business_valid_from": version.business_valid_from,
                "business_valid_to": version.business_valid_to,
            }
        )
    return records


def _amount_pairs(members: list[dict], add, differ: str, match: str) -> None:
    priced = [item for item in members if item.get("amount") is not None]
    for index, left in enumerate(priced):
        for right in priced[index + 1 :]:
            if left["amount"] == right["amount"]:
                add(left, right, "RECONCILES_WITH", match, inferred=False, confidence="high")
            else:
                add(left, right, "CONTRADICTS", differ)


def _is_transaction(external_id: str) -> bool:
    return external_id.split("-", 1)[0] in _TRANSACTION


def _ids(text: str) -> list[str]:
    return [_normalize_id(match) for match in ID_RE.findall(text or "")]
