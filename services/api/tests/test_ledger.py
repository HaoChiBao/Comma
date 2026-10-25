import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.graphstore.ledger import load_ledger_facts, persist_ledger_facts
from app.models import ArtifactVersion, Base, Fact, Organization, SourceArtifact

ZIP = Path(__file__).resolve().parents[1] / "data" / "decagon-agent-safe.zip"

FACT_KEYS = {
    "transaction_external_id",
    "fact_type",
    "amount",
    "currency",
    "source_locator",
    "value_text",
}

NODE_FLAGS = {
    "node",
    "nodes",
    "graph_node",
    "is_node",
    "is_graph_node",
    "as_node",
    "node_id",
    "node_type",
    "draw_node",
    "graph",
}

LEDGER_FILES = {
    "general_ledger_lines.csv",
    "invoice_headers.csv",
    "invoice_lines.csv",
    "purchase_order_headers.csv",
    "purchase_order_lines.csv",
    "bank_transactions.csv",
    "accrual_events.csv",
    "finance_audit_events.csv",
    "master_data.csv",
}


def test_known_purchase_order_and_invoice_amounts():
    facts = load_ledger_facts(ZIP)
    assert facts
    po = _one(facts, "PO-C01-1123", "po_amount")
    assert po["amount"] == Decimal("145.00")
    assert po["currency"] == "USD"
    assert po["source_locator"] == "purchase_order_headers.csv:PO-C01-1123"
    assert po["value_text"] == "145.00 USD"

    invoice = _one(facts, "INV-C01-381", "invoice_amount")
    assert invoice["amount"] == Decimal("158.00")
    assert invoice["currency"] == "USD"
    assert invoice["source_locator"] == "invoice_headers.csv:INV-C01-381"

    line = _one(facts, "INV-C01-381", "invoice_line_amount")
    assert line["amount"] == Decimal("158.00")
    assert line["source_locator"] == "invoice_lines.csv:INV-C01-381-L1"

    posted = _one_locator(facts, "general_ledger_lines.csv:JE-BILL-00001-1")
    assert posted["transaction_external_id"] == "INV-C01-381"
    assert posted["fact_type"] == "gl_amount"
    assert posted["amount"] == Decimal("158.00")
    assert posted["value_text"] == "debit 158.00 USD"

    bank = _one_locator(facts, "bank_transactions.csv:BANK-00001")
    assert bank["transaction_external_id"] == "INV-C01-381"
    assert bank["fact_type"] == "bank_amount"
    assert bank["amount"] == Decimal("-158.00")

    accrual = _one(facts, "ACR-C01-202509", "accrual_amount")
    assert accrual["amount"] == Decimal("150.00")
    assert accrual["source_locator"] == "accrual_events.csv:ACR-C01-202509"

    audit = next(item for item in facts if item["fact_type"] == "audit_event")
    assert audit["amount"] is None
    assert audit["transaction_external_id"].startswith(("INV-", "ACR-"))
    assert audit["source_locator"].startswith("finance_audit_events.csv:")

    for item in facts:
        assert set(item) == FACT_KEYS
        assert NODE_FLAGS.isdisjoint(item)
        assert item["source_locator"].split(":", 1)[0] in LEDGER_FILES
        assert not item["transaction_external_id"].startswith(("VEND-", "CUST-", "COA-", "ACT-", "CC-"))
        assert "oracle/ground_truth.json" not in item["source_locator"]


def test_loader_skips_oracle_ground_truth(tmp_path):
    archive_path = tmp_path / "with-oracle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Decagon_WorldState_Synthetic_Dataset/structured/purchase_order_headers.csv",
            "po_id,amount_usd,currency\nPO-KEEP-1,10.00,USD\n",
        )
        archive.writestr(
            "Decagon_WorldState_Synthetic_Dataset/oracle/ground_truth.json",
            "po_id,amount_usd,currency\nPO-ORACLE-LEAK,999.00,USD\n",
        )
    facts = load_ledger_facts(archive_path)
    assert [item["transaction_external_id"] for item in facts] == ["PO-KEEP-1"]
    assert facts[0]["amount"] == Decimal("10.00")
    leaked = "PO-ORACLE-LEAK"
    assert all(leaked not in str(value) for item in facts for value in item.values())
    again = load_ledger_facts(archive_path)
    assert again == facts


def test_persist_writes_only_existing_versions():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    moment = datetime(2025, 9, 12, tzinfo=timezone.utc)
    facts = [
        {
            "transaction_external_id": "PO-KEEP-1",
            "fact_type": "po_amount",
            "amount": Decimal("10.00"),
            "currency": "USD",
            "source_locator": "purchase_order_headers.csv:PO-KEEP-1",
            "value_text": "10.00 USD",
        },
        {
            "transaction_external_id": "INV-MISSING",
            "fact_type": "invoice_amount",
            "amount": Decimal("99.00"),
            "currency": "USD",
            "source_locator": "invoice_headers.csv:INV-MISSING",
            "value_text": "99.00 USD",
        },
    ]
    with Session(engine) as db:
        db.add(Organization(id="org-test", name="Test"))
        db.add(
            SourceArtifact(
                id="art-po",
                organization_id="org-test",
                kind="purchase_order",
                source_system="test",
                external_id="PO-KEEP-1",
                title="PO-KEEP-1",
            )
        )
        db.add(
            ArtifactVersion(
                id="ver-po",
                artifact_id="art-po",
                version=1,
                content_text="purchase order",
                content_hash="abc",
                document_date=moment,
                business_valid_from=None,
                business_valid_to=None,
                available_from=moment,
                available_to=None,
                ingested_at=moment,
                availability_status="verified",
                availability_evidence="",
                metadata_json={},
            )
        )
        db.flush()
        assert persist_ledger_facts(db, "org-test", facts) == 1
        assert persist_ledger_facts(db, "org-test", facts) == 0
        assert persist_ledger_facts(db, "other-org", facts) == 0
        stored = db.scalars(select(Fact)).all()
        assert len(stored) == 1
        assert stored[0].artifact_version_id == "ver-po"
        assert stored[0].fact_type == "po_amount"
        assert stored[0].amount_decimal == Decimal("10.00")
        assert stored[0].source_locator == "purchase_order_headers.csv:PO-KEEP-1"
        assert [row.external_id for row in db.scalars(select(SourceArtifact)).all()] == ["PO-KEEP-1"]


def _one(facts: list[dict], external_id: str, fact_type: str) -> dict:
    matches = [item for item in facts if item["transaction_external_id"] == external_id and item["fact_type"] == fact_type]
    assert matches
    return matches[0]


def _one_locator(facts: list[dict], source_locator: str) -> dict:
    matches = [item for item in facts if item["source_locator"] == source_locator]
    assert len(matches) == 1
    return matches[0]
