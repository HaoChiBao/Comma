from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db import session
from app.graphstore.claims import build_claims, upsert_claims
from app.graphstore.store import edges_from_claims
from app.main import create_app
from app.models import Claim
from app.seed import seed


def _record(external_id, kind, amount, references, when, entity_ref="c01", version_id=None):
    return {
        "external_id": external_id,
        "version_id": version_id or external_id,
        "kind": kind,
        "amount": amount,
        "references": references,
        "available_from": when,
        "entity_ref": entity_ref,
    }


def test_one_contradiction_for_one_transaction():
    when = datetime(2025, 9, 20, tzinfo=timezone.utc)
    records = [
        _record("PO-1", "purchase_order", "100.00", ["PO-1"], when),
        _record("INV-9", "invoice", "120.00", ["PO-1"], when),
        _record("MSG-9", "email", "130.00", ["PO-1"], when),
    ]
    contradictions = [claim for claim in build_claims(records) if claim["claim_type"] == "contradicts"]
    assert len(contradictions) == 1
    assert contradictions[0]["from_node_id"] == "PO-1"
    assert contradictions[0]["to_node_id"] == "PO-1"
    assert len(contradictions[0]["evidence_version_ids"]) == 3


def test_ambiguous_cost_stores_every_formula_on_one_claim():
    early = datetime(2025, 9, 30, 17, tzinfo=timezone.utc)
    later = datetime(2025, 10, 7, tzinfo=timezone.utc)
    records = [
        _record("CTR-0009", "contract", "4700.00", ["CTR-0009"], early, "c09"),
        _record("MSG-0009", "email", "4700.00", ["MSG-0009"], early, "c09"),
        _record("PO-C09-1123", "purchase_order", "4700.00", ["PO-C09-1123"], early, "c09"),
        _record("ACR-C09-202509", "journal", "4400.00", ["ACR-C09-202509"], early, "c09"),
        _record("JE-ADJ-C09", "journal", "300.00", ["JE-ADJ-C09"], later, "c09"),
    ]
    derived = [claim for claim in build_claims(records) if claim["claim_type"] == "derived_from" and claim["from_node_id"] == "JE-ADJ-C09"]
    assert len(derived) == 1
    assert derived[0]["status"] == "ambiguous"
    assert len(derived[0]["formulas"]) == 3
    assert "CTR-0009 minus ACR-C09-202509" in derived[0]["formulas"]
    assert "The source is ambiguous." in derived[0]["explanation"]


def test_later_invoice_does_not_explain_an_earlier_accrual():
    records = [
        _record("PO-C01-1123", "purchase_order", "145.00", ["PO-C01-1123"], datetime(2025, 9, 13, 11, tzinfo=timezone.utc)),
        _record("ACR-C01-202509", "journal", "150.00", ["ACR-C01-202509"], datetime(2025, 9, 30, 17, tzinfo=timezone.utc)),
        _record("INV-C01-381", "invoice", "158.00", ["PO-C01-1123"], datetime(2025, 12, 10, 10, tzinfo=timezone.utc), version_id="inv-version"),
    ]
    claims = build_claims(records)
    accrual = [claim for claim in claims if claim["from_node_id"] == "ACR-C01-202509" and claim["claim_type"] in {"derived_from", "source_candidate", "unexplained"}]
    assert accrual
    assert all(claim["claim_type"] != "derived_from" for claim in accrual)
    assert all("inv-version" not in claim["evidence_version_ids"] for claim in accrual)
    assert all("158" not in claim["explanation"] for claim in accrual)


def test_separate_transactions_are_not_one_contradiction():
    when = datetime(2025, 9, 20, tzinfo=timezone.utc)
    records = [
        _record("PO-A", "purchase_order", "700.00", ["PO-A"], when, "vendor-apex"),
        _record("PO-B", "purchase_order", "100.00", ["PO-B"], when, "vendor-other"),
    ]
    assert not any(claim["claim_type"] == "contradicts" for claim in build_claims(records))


def test_hidden_evidence_is_left_out_of_the_graph():
    early = datetime(2025, 9, 30, 17, tzinfo=timezone.utc)
    later = datetime(2025, 12, 10, 10, tzinfo=timezone.utc)
    records = [
        _record("ACR-C01-202509", "journal", "150.00", ["ACR-C01-202509"], early, version_id="accrual-version"),
        _record("INV-C01-381", "invoice", "158.00", ["INV-C01-381"], later, version_id="inv-version"),
    ]
    claims = build_claims(records)
    visible = {record["version_id"] for record in records if record["external_id"] != "INV-C01-381"}
    edges = edges_from_claims(claims, [record for record in records if record["version_id"] in visible], early, visible)
    assert "158" not in str(edges)


def test_upsert_claims_keeps_one_row_per_evidence_hash():
    create_app("sqlite:///:memory:")
    when = datetime(2025, 9, 20, tzinfo=timezone.utc)
    claims = build_claims(
        [
            _record("PO-1", "purchase_order", "100.00", ["PO-1"], when),
            _record("INV-9", "invoice", "120.00", ["PO-1"], when),
        ]
    )
    with session() as db:
        seed(db)
        upsert_claims(db, "org-acme", claims)
        upsert_claims(db, "org-acme", claims)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Claim)) == len({claim["evidence_hash"] for claim in claims})
