from datetime import datetime, timezone
from decimal import Decimal

from app.relationships import find_relationships


def _record(external_id, kind, amount, references, case_ids=None, entity_ref="c01"):
    return {
        "external_id": external_id,
        "version_id": external_id.lower(),
        "kind": kind,
        "amount": None if amount is None else Decimal(amount),
        "references": references,
        "case_ids": case_ids or [],
        "entity_ref": entity_ref,
        "available_from": datetime(2025, 9, 30, 17, tzinfo=timezone.utc),
        "business_valid_from": None,
        "business_valid_to": None,
    }


def test_relationships_come_from_the_documents():
    records = [
        _record("PO-C01-1123", "purchase_order", "145.00", ["PO-C01-1123"], ["C01"]),
        _record("MSG-0001", "email", "160.00", ["MSG-0001", "PO-C01-1123", "INV-C01-381"], ["C01"]),
        _record("INV-C01-381", "invoice", "158.00", ["INV-C01-381", "PO-C01-1123"], ["C01"]),
        _record("ACR-C01-202509", "journal", "150.00", ["ACR-C01-202509"], ["C01"]),
    ]
    found = find_relationships(records)
    kinds = {(item["type"], frozenset((item["source_label"], item["target_label"]))) for item in found}
    assert ("MENTIONS", frozenset(("MSG-0001", "PO-C01-1123"))) in kinds
    assert ("MENTIONS", frozenset(("INV-C01-381", "PO-C01-1123"))) in kinds
    assert ("CONTRADICTS", frozenset(("PO-C01-1123", "INV-C01-381"))) in kinds
    assert ("CONTRADICTS", frozenset(("ACR-C01-202509", "PO-C01-1123"))) in kinds
    assert all(not item["id"].startswith("ge-") for item in found)
    separate = [
        _record("PO-A", "purchase_order", "145.00", ["PO-A"], ["CASE-008"], "vendor-a"),
        _record("PO-B", "purchase_order", "700.00", ["PO-B"], ["CASE-008"], "vendor-b"),
    ]
    assert not any(item["type"] == "CONTRADICTS" for item in find_relationships(separate))
    assert not any(item["type"] == "AMBIGUOUS" for item in find_relationships(separate))


def test_cost_origin_uses_only_earlier_amounts():
    early = datetime(2025, 9, 13, tzinfo=timezone.utc)
    close = datetime(2025, 9, 30, 17, tzinfo=timezone.utc)
    later = datetime(2025, 10, 7, tzinfo=timezone.utc)
    records = [
        _record("PO-C09-1123", "purchase_order", "4700.00", ["PO-C09-1123"], ["C09"], "c09"),
        _record("ACR-C09-202509", "journal", "4400.00", ["ACR-C09-202509"], ["C09"], "c09"),
        _record("JE-ADJ-C09", "journal", "300.00", ["JE-ADJ-C09"], ["C09"], "c09"),
        _record("INV-C09-381", "invoice", "4720.00", ["INV-C09-381", "PO-C09-1123"], ["C09"], "c09"),
    ]
    records[0]["available_from"] = early
    records[1]["available_from"] = close
    records[2]["available_from"] = later
    records[3]["available_from"] = datetime(2025, 12, 10, tzinfo=timezone.utc)
    found = find_relationships(records)
    derived = [item for item in found if item["type"] == "DERIVED_FROM" and "JE-ADJ-C09" in {item["source_label"], item["target_label"]}]
    assert derived
    assert all("plus" not in item["explanation"] for item in derived)
    assert any("minus" in item["explanation"] for item in derived)
    accrual = [item for item in found if item["type"] in {"DERIVED_FROM", "SOURCE_CANDIDATE"} and item["target_label"] == "ACR-C09-202509"]
    assert not accrual


def test_several_matching_amounts_are_ambiguous():
    records = [
        _record("PO-1", "purchase_order", "145.00", ["PO-1"]),
        _record("CTR-1", "contract", "145.00", ["CTR-1"]),
        _record("ACR-1", "journal", "145.00", ["ACR-1"]),
    ]
    records[2]["available_from"] = datetime(2025, 9, 30, 18, tzinfo=timezone.utc)
    found = find_relationships(records)
    ambiguous = [item for item in found if item["type"] == "AMBIGUOUS" and item["target_label"] == "ACR-1"]
    assert len(ambiguous) >= 2
    assert not any(item["type"] == "SOURCE_CANDIDATE" and item["target_label"] == "ACR-1" for item in found)
