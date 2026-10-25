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
