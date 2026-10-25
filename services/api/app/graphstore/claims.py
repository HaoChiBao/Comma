"""Claims read from documents that are already in scope.

One transaction with disagreeing amounts is one contradiction. One cost is one
derivation, even when several formulas fit. A document that arrives later is
not evidence for an earlier cost. Case ids are not a shared transaction.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.temporal import aware, canonical, sha256_text

_TRANSACTION_PREFIXES = {"PO", "INV", "JE", "EM", "MSG", "CTR", "AMD", "SVC", "ACR"}


def build_claims(records: list[dict]) -> list[dict]:
    normalized = _dedupe(_normalize(record) for record in records)
    claims = [
        *_mentions(normalized),
        *_contradictions(normalized),
        *_origins(normalized),
    ]
    unique: dict[str, dict] = {}
    for claim in claims:
        unique.setdefault(claim["evidence_hash"], claim)
    return sorted(unique.values(), key=lambda claim: (claim["claim_type"], claim["from_node_id"], claim["to_node_id"], claim["evidence_hash"]))


def upsert_claims(db: Session, organization_id: str, claims: list[dict]) -> list:
    """Insert claim rows, or update the row that already has this evidence hash.

    The caller commits. If the Claim model is not on app.models yet, this raises
    RuntimeError and build_claims still stands on its own.
    """
    claim_model = _claim_model()
    if claim_model is None:
        raise RuntimeError("Claim model is not available")
    saved: dict[str, object] = {}
    for claim in claims:
        digest = claim["evidence_hash"]
        row = saved.get(digest)
        if row is None:
            row = db.scalar(select(claim_model).where(claim_model.evidence_hash == digest))
        if row is None:
            row = claim_model(id=digest, organization_id=organization_id, evidence_hash=digest)
            db.add(row)
        _assign(row, organization_id, claim)
        saved[digest] = row
    db.flush()
    return list(saved.values())


def _claim_model():
    try:
        from app import models
    except ImportError:
        return None
    return getattr(models, "Claim", None)


def _assign(row, organization_id: str, claim: dict) -> None:
    row.organization_id = organization_id
    row.claim_type = claim["claim_type"]
    row.from_node_id = claim["from_node_id"]
    row.to_node_id = claim["to_node_id"]
    row.status = claim["status"]
    row.evidence_version_ids = list(claim["evidence_version_ids"])
    row.formulas = list(claim["formulas"])
    row.knowable_from = claim["knowable_from"]
    row.evidence_hash = claim["evidence_hash"]
    row.explanation = claim["explanation"]
    row.is_inferred = bool(claim["is_inferred"])
    row.confidence = claim["confidence"]


def _mentions(records: list[dict]) -> list[dict]:
    by_external: dict[str, list[dict]] = {}
    for record in records:
        by_external.setdefault(record["external_id"], []).append(record)
    claims = []
    for record in records:
        if record["available_from"] is None:
            continue
        cited = []
        seen: set[str] = set()
        for ref in record["references"]:
            if ref == record["external_id"] or ref in seen or ref not in by_external:
                continue
            seen.add(ref)
            cited.append(ref)
        for ref in cited:
            targets = [item for item in by_external[ref] if item["available_from"] is not None]
            if not targets:
                continue
            evidence = [record, *targets]
            claim = _make(
                claim_type="mentions",
                from_node_id=record["external_id"],
                to_node_id=ref,
                status="exact",
                evidence=evidence,
                formulas=[],
                knowable_from=_later(evidence),
                explanation=f"{record['external_id']} names {ref}. That does not prove it was used.",
                is_inferred=False,
                confidence="high",
            )
            if claim is not None:
                claims.append(claim)
    return claims


def _contradictions(records: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        if record["amount"] is None:
            continue
        for transaction_id in _transaction_ids(record):
            grouped.setdefault(transaction_id, []).append(record)
    claims = []
    for transaction_id, members in grouped.items():
        priced = [item for item in _unique(members) if item["available_from"] is not None]
        amounts = {item["amount"] for item in priced}
        if len(amounts) < 2:
            continue
        knowable = _later(priced)
        if knowable is None:
            continue
        claims.append(
            _make(
                claim_type="contradicts",
                from_node_id=transaction_id,
                to_node_id=transaction_id,
                status="exact",
                evidence=priced,
                formulas=[],
                knowable_from=knowable,
                explanation=f"The amounts on {transaction_id} disagree.",
                is_inferred=False,
                confidence="high",
            )
        )
    return claims


def _origins(records: list[dict]) -> list[dict]:
    claims = []
    for cost in records:
        if cost["amount"] is None or cost["available_from"] is None:
            continue
        priors = [
            item
            for item in records
            if item["version_id"] != cost["version_id"]
            and item["amount"] is not None
            and item["available_from"] is not None
            and item["available_from"] <= cost["available_from"]
            and _tied(cost, item)
        ]
        claim = _explain(cost, _unique(priors))
        if claim is not None:
            claims.append(claim)
    return claims


def _explain(cost: dict, priors: list[dict]) -> dict | None:
    exact = [item for item in priors if item["amount"] == cost["amount"]]
    formulas = _pair_formulas(cost, priors)
    if len(formulas) == 1 and not exact:
        return _derived(cost, formulas, exact=[], status="exact")
    if len(formulas) > 1 or (formulas and exact):
        return _derived(cost, formulas, exact=exact, status="ambiguous")
    if len(exact) == 1:
        return _source(cost, exact, status="exact")
    if len(exact) > 1:
        return _source(cost, exact, status="ambiguous")
    if cost["kind"].lower() == "journal":
        return _unexplained(cost)
    return None


def _derived(cost: dict, formulas: list[dict], exact: list[dict], status: str) -> dict | None:
    inputs = []
    seen: set[str] = set()
    for formula in formulas:
        for item in (formula["left"], formula["right"]):
            if item["version_id"] not in seen:
                seen.add(item["version_id"])
                inputs.append(item)
    for item in exact:
        if item["version_id"] not in seen:
            seen.add(item["version_id"])
            inputs.append(item)
    texts = [formula["text"] for formula in formulas]
    if status == "exact":
        explanation = f"{cost['external_id']} equals {texts[0]}. No formula in the text says that."
        confidence = "medium"
    else:
        explanation = f"{cost['external_id']} has more than one possible source: {'; '.join(sorted(texts))}. The source is ambiguous."
        if exact:
            names = ", ".join(sorted({item["external_id"] for item in exact}))
            explanation = (
                f"{cost['external_id']} has more than one possible source: {'; '.join(sorted(texts))}; "
                f"also matches {names}. The source is ambiguous."
            )
        confidence = "low"
    return _make(
        claim_type="derived_from",
        from_node_id=cost["external_id"],
        to_node_id=cost["external_id"],
        status=status,
        evidence=[cost, *inputs],
        formulas=texts,
        knowable_from=_later([cost, *inputs]),
        explanation=explanation,
        is_inferred=True,
        confidence=confidence,
    )


def _source(cost: dict, exact: list[dict], status: str) -> dict | None:
    if status == "exact":
        prior = exact[0]
        explanation = (
            f"{cost['external_id']} matches {prior['external_id']}, which was already available. "
            "That does not prove it was the source."
        )
        return _make(
            claim_type="source_candidate",
            from_node_id=prior["external_id"],
            to_node_id=cost["external_id"],
            status="exact",
            evidence=[prior, cost],
            formulas=[],
            knowable_from=_later([prior, cost]),
            explanation=explanation,
            is_inferred=True,
            confidence="medium",
        )
    names = ", ".join(sorted({item["external_id"] for item in exact}))
    return _make(
        claim_type="source_candidate",
        from_node_id=cost["external_id"],
        to_node_id=cost["external_id"],
        status="ambiguous",
        evidence=[cost, *exact],
        formulas=[],
        knowable_from=_later([cost, *exact]),
        explanation=f"{cost['external_id']} matches more than one earlier amount ({names}) and no unique formula. The source is ambiguous.",
        is_inferred=True,
        confidence="low",
    )


def _unexplained(cost: dict) -> dict | None:
    if cost["available_from"] is None:
        return None
    return _make(
        claim_type="unexplained",
        from_node_id=cost["external_id"],
        to_node_id=cost["external_id"],
        status="unexplained",
        evidence=[cost],
        formulas=[],
        knowable_from=cost["available_from"],
        explanation=f"No earlier visible amount or sum equals {cost['external_id']}, so its origin is unexplained.",
        is_inferred=False,
        confidence="low",
    )


def _pair_formulas(cost: dict, priors: list[dict]) -> list[dict]:
    found: dict[str, dict] = {}
    target = cost["amount"]
    for index, left in enumerate(priors):
        for right in priors[index + 1 :]:
            if left["amount"] + right["amount"] == target:
                first, second = sorted((left, right), key=lambda item: item["external_id"])
                text = f"{first['external_id']} plus {second['external_id']}"
                found.setdefault(text, {"text": text, "left": first, "right": second})
            if left["amount"] - right["amount"] == target:
                text = f"{left['external_id']} minus {right['external_id']}"
                found.setdefault(text, {"text": text, "left": left, "right": right})
            if right["amount"] - left["amount"] == target:
                text = f"{right['external_id']} minus {left['external_id']}"
                found.setdefault(text, {"text": text, "left": right, "right": left})
    return [found[key] for key in sorted(found)]


def _tied(cost: dict, other: dict) -> bool:
    """Same record, a citation, or a shared transaction. Case id is not a tie."""
    if cost["entity_ref"] and cost["entity_ref"] == other["entity_ref"]:
        return True
    refs = set(cost["references"])
    other_refs = set(other["references"])
    if other["external_id"] in refs or cost["external_id"] in other_refs:
        return True
    return bool(_transaction_ids(cost) & _transaction_ids(other))


def _make(
    *,
    claim_type: str,
    from_node_id: str,
    to_node_id: str,
    status: str,
    evidence: list[dict],
    formulas: list[str],
    knowable_from: datetime | None,
    explanation: str,
    is_inferred: bool,
    confidence: str,
) -> dict | None:
    if knowable_from is None:
        return None
    evidence_ids = sorted({item["version_id"] for item in evidence})
    formula_text = sorted(formulas)
    payload = {
        "type": claim_type,
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "status": status,
        "evidence_version_ids": evidence_ids,
        "formulas": formula_text,
    }
    return {
        "claim_type": claim_type,
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "status": status,
        "evidence_version_ids": evidence_ids,
        "formulas": formula_text,
        "knowable_from": knowable_from,
        "evidence_hash": sha256_text(canonical(payload)),
        "explanation": explanation,
        "is_inferred": is_inferred,
        "confidence": confidence,
    }


def _transaction_ids(record: dict) -> set[str]:
    found = set()
    if _is_transaction(record["external_id"]):
        found.add(record["external_id"])
    for ref in record["references"]:
        if _is_transaction(ref):
            found.add(ref)
    return found


def _is_transaction(external_id: str) -> bool:
    return external_id.split("-", 1)[0] in _TRANSACTION_PREFIXES


def _later(records: list[dict]) -> datetime | None:
    times = [item["available_from"] for item in records if item.get("available_from") is not None]
    if not times:
        return None
    return max(times)


def _unique(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    chosen = []
    for record in records:
        if record["version_id"] in seen:
            continue
        seen.add(record["version_id"])
        chosen.append(record)
    return chosen


def _dedupe(records) -> list[dict]:
    return _unique(list(records))


def _normalize(record: dict) -> dict:
    return {
        "external_id": str(record.get("external_id") or ""),
        "version_id": str(record.get("version_id") or record.get("external_id") or ""),
        "kind": str(record.get("kind") or ""),
        "amount": _amount(record.get("amount")),
        "references": [str(item) for item in (record.get("references") or [])],
        "available_from": _when(record.get("available_from")),
        "entity_ref": str(record.get("entity_ref") or ""),
    }


def _amount(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount.quantize(Decimal("0.01"))


def _when(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return aware(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return aware(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None
