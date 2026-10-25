"""Relationships found by reading documents. Nothing in here comes from a supplied edge list."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.parse import ID_RE, _normalize_id
from app.models import IngestRecord
from app.temporal import aware, interval_contains

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
        group = list(members.values())
        _amount_pairs(
            group,
            add,
            "These documents refer to the same record and the amounts differ.",
            "These documents refer to the same record and the amounts match.",
        )
        _infer_origins(group, add)
        _ambiguous_peers(group, add)

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
        _infer_origins(group, add)
        _ambiguous_peers(group, add)
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


def _infer_origins(members: list[dict], add) -> None:
    priced = [item for item in members if item.get("amount") is not None and item.get("available_from") is not None]
    for cost in priced:
        priors = [item for item in priced if item["external_id"] != cost["external_id"] and _related(cost, item) and _at_or_before(item, cost)]
        exact = [item for item in priors if item["amount"] == cost["amount"]]
        sums = []
        for index, left in enumerate(priors):
            for right in priors[index + 1 :]:
                if left["amount"] + right["amount"] == cost["amount"]:
                    sums.append((left, right, "plus"))
                elif left["amount"] - right["amount"] == cost["amount"]:
                    sums.append((left, right, "minus"))
                elif right["amount"] - left["amount"] == cost["amount"]:
                    sums.append((right, left, "minus"))
        if len(exact) == 1 and not sums:
            prior = exact[0]
            add(
                prior,
                cost,
                "SOURCE_CANDIDATE",
                f"{cost['external_id']} matches {prior['external_id']}, which was already available. That does not prove it was the source.",
            )
        elif exact or len(sums) > 1:
            choices = [item["external_id"] for item in exact]
            for left, right, how in sums:
                if how == "plus":
                    choices.append(f"{left['external_id']} plus {right['external_id']}")
                else:
                    choices.append(f"{left['external_id']} minus {right['external_id']}")
            text = f"{cost['external_id']} has more than one possible source: {'; '.join(choices)}. The source is ambiguous."
            involved = list(exact)
            for left, right, _how in sums:
                involved.extend([left, right])
            seen_ids = set()
            for item in involved:
                if item["external_id"] in seen_ids:
                    continue
                seen_ids.add(item["external_id"])
                add(item, cost, "AMBIGUOUS", text)
        elif len(sums) == 1:
            left, right, how = sums[0]
            if how == "plus":
                text = f"{cost['external_id']} equals {left['external_id']} plus {right['external_id']}. No formula in the text says they were added."
            else:
                text = f"{cost['external_id']} equals {left['external_id']} minus {right['external_id']}. No formula in the text says that."
            add(left, cost, "DERIVED_FROM", text)
            add(right, cost, "DERIVED_FROM", text)


def _ambiguous_peers(members: list[dict], add) -> None:
    by_kind: dict[str, list[dict]] = {}
    for item in members:
        if item.get("amount") is None:
            continue
        by_kind.setdefault(item["kind"], []).append(item)
    for kind, group in by_kind.items():
        if kind not in {"email", "journal", "invoice", "purchase_order", "contract"} or len(group) < 2:
            continue
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if left.get("entity_ref") != right.get("entity_ref") or not left.get("entity_ref"):
                    continue
                if left["amount"] == right["amount"]:
                    continue
                if right["external_id"] in (left.get("references") or []) or left["external_id"] in (right.get("references") or []):
                    continue
                add(
                    left,
                    right,
                    "AMBIGUOUS",
                    "These documents are the same kind and neither names the other. Which one a cost used is ambiguous.",
                )


def _related(cost: dict, other: dict) -> bool:
    if cost.get("entity_ref") and cost.get("entity_ref") == other.get("entity_ref"):
        return True
    return other["external_id"] in (cost.get("references") or []) or cost["external_id"] in (other.get("references") or [])


def _at_or_before(prior: dict, cost: dict) -> bool:
    return aware(prior["available_from"]) <= aware(cost["available_from"])


def _is_transaction(external_id: str) -> bool:
    return external_id.split("-", 1)[0] in _TRANSACTION


def _ids(text: str) -> list[str]:
    return [_normalize_id(match) for match in ID_RE.findall(text or "")]
