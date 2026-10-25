import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["INGEST_INLINE"] = "1"

from app.main import create_app  # noqa: E402

TRUTH = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "truth.json").read_text())
SEPT = TRUTH["as_of"]["september"]
DEC = TRUTH["as_of"]["december"]
WORKBOOK = TRUTH["workbook_id"]
SHEET = TRUTH["sheet_name"]
FINANCE = "actor-decagon-finance"
AUDITOR = "actor-decagon-auditor"
LEGAL = "actor-decagon-legal"
IDS = {"trace_id", "snapshot_id", "manifest_hash", "request_id", "source_id"}


@pytest.fixture(scope="module")
def company_db(tmp_path_factory) -> Path:
    """Ingest the Decagon pack once; each test gets its own copy of the database."""
    path = tmp_path_factory.mktemp("decagon") / "company.db"
    create_app(f"sqlite:///{path}")
    from app.db import engine, session
    from app.graphstore.store import ensure_company_index
    from app.ingest.decagon import start_decagon
    from app.seed import seed

    with session() as db:
        seed(db)
        assert start_decagon(db).status == "completed"
        ensure_company_index(db, "org-decagon")
    engine().dispose()
    return path


@pytest.fixture
def api(company_db, tmp_path) -> TestClient:
    copy = tmp_path / "company.db"
    shutil.copy(company_db, copy)
    return TestClient(create_app(f"sqlite:///{copy}"))


def inspect(api, address, raw_value, as_of=SEPT, formula="", precedents=(), **extra):
    body = {
        "workbook_id": WORKBOOK,
        "sheet_name": SHEET,
        "cell_address": address,
        "raw_value": raw_value,
        "display_text": f"${raw_value:,.2f}",
        "formula": formula,
        "direct_precedents": list(precedents),
        "as_of": as_of,
        **extra,
    }
    response = api.post("/v1/traces/inspect", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def without_ids(body) -> str:
    """Random ids and hashes are hex and can contain '158' by chance."""
    if isinstance(body, dict):
        return json.dumps({key: json.loads(without_ids(value)) for key, value in body.items() if key not in IDS})
    if isinstance(body, list):
        return json.dumps([json.loads(without_ids(item)) for item in body])
    return json.dumps(body)


def sheet_cells():
    """What the add-in reads from the workbook: the PO amounts, the accruals, and a header."""
    from app.ingest.decagon import PACK
    from app.ingest.decagon_workbook import workbook_rows

    cells = [{"address": "A1", "raw_value": "Case"}]
    for row in workbook_rows(PACK):
        if row["po_amount"] is not None:
            cells.append({"address": f"E{row['row']}", "raw_value": float(row["po_amount"])})
            cells.append({"address": f"G{row['row']}", "raw_value": float(row["accrual_amount"] - row["po_amount"]), "formula": f"=F{row['row']}-E{row['row']}"})
        cells.append({"address": f"F{row['row']}", "raw_value": float(row["accrual_amount"])})
    return cells


def by_id(body, external_id):
    return [item for item in body["evidence"] if item.get("external_id") == external_id]


def test_c01_september_is_review_against_the_vendor_email(api):
    body = inspect(api, "F2", 150)
    assert body["status"] == "review"
    assert body["title"] == "Potential estimate discrepancy"
    assert body["difference"] == {"amount": "10.00", "currency": "USD"}
    assert body["case_id"] == "C01"
    assert "MSG-0001" in body["summary"] and "may be reasonable" in body["summary"].lower()
    [email] = by_id(body, "MSG-0001")
    assert (email["relationship"], email["link_status"], email["amount"]) == ("CONFLICTS_WITH", "candidate", "160.00")
    assert email["basis"] == "MSG-0001 names PO-C01-1123"
    [order] = by_id(body, "PO-C01-1123")
    assert (order["link_status"], order["amount"], order["ledger_locator"]) == ("verified", "145.00", "purchase_order_headers.csv:PO-C01-1123")
    [posted] = by_id(body, "ACR-C01-202509")
    assert (posted["relationship"], posted["comparison"]) == ("RECORDED_AS", "match")
    assert posted["ledger_locator"] == "accrual_events.csv:ACR-C01-202509"
    assert body["origin"]["claim_type"] == "unexplained"
    assert any("No proof that the preparer read" in item for item in body["unknowns"])


def test_september_trace_withholds_all_invoice_content(api):
    body = inspect(api, "F2", 150)
    excluded = [item for item in body["evidence"] if item["status"] == "excluded"]
    assert excluded and all(item["reason"] == "FUTURE_INFORMATION" for item in excluded)
    assert all(set(item) == {"cell", "source_id", "status", "reason", "relationship", "link_status"} for item in excluded)
    text = without_ids(body)
    for leaked in ("158", "Amount due", "2025-12-10", "INV-C01-381.pdf", "BANK-00001"):
        assert leaked not in text
    graph = api.get(f"/v1/traces/{body['trace_id']}/graph")
    assert graph.status_code == 200
    assert "158" not in without_ids(graph.json())
    blocked = [node for node in graph.json()["nodes"] if node["type"] == "Blocked"]
    assert blocked and all(node["amount"] is None and node["source_id"] is None for node in blocked)


def test_direct_source_read_is_sealed_to_the_trace(api):
    september = inspect(api, "F2", 150)
    invoice_version = next(item["source_id"] for item in september["evidence"] if item["status"] == "excluded" and item["link_status"] == "verified")
    denied = api.get(f"/v1/sources/{invoice_version}?trace_id={september['trace_id']}")
    assert denied.status_code == 403
    assert denied.json()["code"] == "FUTURE_INFORMATION"
    assert denied.json()["request_id"]
    assert "158" not in without_ids(denied.json())
    december = inspect(api, "F2", 150, DEC)
    opened = api.get(f"/v1/sources/{invoice_version}?trace_id={december['trace_id']}")
    assert opened.status_code == 200
    assert opened.json()["external_id"] == "INV-C01-381"
    assert any(fact["locator"] == "invoice_headers.csv:INV-C01-381" and fact["amount"] == "158.00" for fact in opened.json()["facts"])


def test_december_trace_exposes_the_invoice(api):
    body = inspect(api, "F2", 150, DEC)
    [invoice] = by_id(body, "INV-C01-381")
    assert invoice["status"] == "available"
    assert invoice["amount"] == "158.00"
    assert invoice["relationship"] == "CONFLICTS_WITH"
    assert invoice["document_date"].startswith("2025-09-28")
    assert invoice["available_at"].startswith("2025-12-10")


def test_restricted_contract_follows_the_role(api):
    finance = inspect(api, "F8", 1200, actor_id=FINANCE)
    assert finance["status"] == "supported"
    assert not by_id(finance, "CTR-0007")
    assert any(item["status"] == "excluded" and item["reason"] == "INSUFFICIENT_PERMISSION" for item in finance["evidence"])
    auditor = inspect(api, "F8", 1200, actor_id=AUDITOR)
    assert auditor["status"] == "review"
    assert auditor["title"] == "Amount differs from the contract"
    assert by_id(auditor, "CTR-0007")[0]["amount"] == "2100.00"
    legal = inspect(api, "F8", 1200, actor_id=LEGAL)
    assert by_id(legal, "CTR-0007")[0]["status"] == "available"
    # Legal cannot read the purchase order, the email, or the posted journal.
    assert not by_id(legal, "PO-C07-1123") and not by_id(legal, "ACR-C07-202509")


def test_missing_purchase_order_is_called_out(api):
    body = inspect(api, "F5", 2250)
    assert "No purchase order is on file for this accrual." in body["unknowns"]
    assert body["origin"]["claim_type"] == "source_candidate"
    assert body["origin"]["evidence"] == ["MSG-0004"]


def test_origin_claim_with_withheld_evidence_is_dropped(api):
    from app.db import session
    from app.models import ArtifactVersion, Claim
    from app.temporal import utc

    with session() as db:
        accrual = db.query(ArtifactVersion).join(ArtifactVersion.artifact).filter_by(external_id="ACR-C01-202509").one()
        invoice = db.query(ArtifactVersion).join(ArtifactVersion.artifact).filter_by(external_id="INV-C01-381").one()
        db.add(
            Claim(
                id="planted",
                organization_id="org-decagon",
                claim_type="derived_from",
                from_node_id="ACR-C01-202509",
                to_node_id="ACR-C01-202509",
                status="exact",
                evidence_version_ids=[accrual.id, invoice.id],
                formulas=["INV-C01-381 minus 8"],
                knowable_from=utc("2025-09-30T17:00:00+00:00"),
                evidence_hash="planted",
                explanation="Planted claim that leans on the December invoice.",
                is_inferred=True,
                confidence="low",
            )
        )
        db.commit()
    body = inspect(api, "F2", 150)
    assert body["origin"]["claim_type"] == "unexplained"
    assert "Planted" not in without_ids(body)


def test_formula_cell_takes_the_status_of_its_inputs(api):
    body = inspect(api, "G2", 5, formula="=F2-E2", precedents=["Accruals!F2", "Accruals!E2"], precedent_values={"Accruals!F2": 150, "Accruals!E2": 145})
    assert body["status"] == "review"
    assert body["title"] == "Depends on F2"
    assert body["lineage"] == [
        {"from": "Accruals!F2", "to": "Accruals!G2", "kind": "FORMULA_DEPENDS_ON", "verified": True},
        {"from": "Accruals!E2", "to": "Accruals!G2", "kind": "FORMULA_DEPENDS_ON", "verified": True},
    ]
    graph = api.get(f"/v1/traces/{body['trace_id']}/graph").json()
    assert {"FORMULA_DEPENDS_ON", "MENTIONS", "RECORDED_AS"} <= {edge["type"] for edge in graph["edges"]}


def test_po_cell_is_supported_by_its_purchase_order(api):
    body = inspect(api, "E2", 145)
    assert body["status"] == "supported"
    assert body["title"] == "Supported by PO-C01-1123"
    assert body["origin"] is None


def test_changed_cell_value_is_compared_live(api):
    body = inspect(api, "F7", 3600)
    assert body["status"] == "review"
    assert body["title"] == "Cell differs from the posted accrual"
    assert body["difference"]["amount"] == "100.00"


@pytest.mark.parametrize("actor", [FINANCE, AUDITOR])
def test_scan_matches_ground_truth(api, actor):
    for label, as_of in TRUTH["as_of"].items():
        expected = TRUTH["flags"][actor].get(label)
        if expected is None:
            continue
        response = api.post("/v1/workbooks/scan", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "range_address": TRUTH["scan_range"], "cells": sheet_cells(), "as_of": as_of, "actor_id": actor})
        assert response.status_code == 200, response.text
        findings = response.json()["findings"]
        flagged = {item["address"]: item["status"] for item in findings if item["status"] != "supported"}
        assert flagged == {address: flag["status"] for address, flag in expected.items()}, label
        statuses = {item["address"]: item["status"] for item in findings}
        assert all(statuses[address] == "supported" for address in TRUTH["controls"].get(actor, {}).get(label, []))
        assert "158" not in without_ids(findings) or label == "december"


def test_organization_snapshot_is_sealed_and_idempotent(api):
    from app.db import session
    from app.snapshots import create_org_snapshot
    from app.temporal import utc

    with session() as db:
        first = create_org_snapshot(db, actor_id=FINANCE, as_of=utc(SEPT))
        second = create_org_snapshot(db, actor_id=FINANCE, as_of=utc(SEPT))
        later = create_org_snapshot(db, actor_id=FINANCE, as_of=utc(DEC))
        assert first.id == second.id and first.manifest_hash == second.manifest_hash
        assert first.case_id is None
        assert len(later.allowed_manifest_json) > len(first.allowed_manifest_json)


def test_errors_are_typed(api):
    bad = api.post("/v1/traces/inspect", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "cell_address": "A1:B2", "raw_value": 1, "as_of": SEPT})
    assert bad.status_code == 400
    assert bad.json()["code"] == "INVALID_CELL"
    assert bad.json()["request_id"]
    missing = api.get("/v1/traces/trc_missing/graph")
    assert missing.status_code == 404
    assert missing.json()["code"] == "TRACE_NOT_FOUND"
    malformed = api.post("/v1/traces/inspect", json={"workbook_id": WORKBOOK})
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "INVALID_REQUEST"
    unknown_book = api.post("/v1/traces/inspect", json={"workbook_id": "nope", "sheet_name": SHEET, "cell_address": "F2", "as_of": SEPT})
    assert unknown_book.status_code == 404
    too_many = api.post("/v1/workbooks/scan", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "cells": [{"address": "A1"}] * 201, "as_of": SEPT})
    assert too_many.status_code == 400
    assert too_many.json()["code"] == "RANGE_TOO_LARGE"


def test_other_organization_cannot_trace_the_workbook(api):
    response = api.post("/v1/traces/inspect", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "cell_address": "F2", "raw_value": 150, "as_of": SEPT, "actor_id": "actor-maya"})
    assert response.status_code == 403
    assert response.json()["code"] == "OUTSIDE_ORGANIZATION"


def test_workbook_is_not_ready_before_company_ingest():
    api = TestClient(create_app("sqlite:///:memory:"))
    from app.db import session
    from app.ingest.decagon import ensure_company
    from app.seed import seed

    with session() as db:
        seed(db)
        ensure_company(db)
    response = api.post("/v1/traces/inspect", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "cell_address": "F2", "raw_value": 150, "as_of": SEPT})
    assert response.status_code == 503
    assert response.json()["code"] == "INDEX_NOT_READY"


def test_startup_indexing_waits_for_the_company_ingest(api):
    from app.db import session
    from app.main import _wait_for_ingest
    from app.models import IngestJob

    assert _wait_for_ingest("org-decagon", timeout=1, interval=0.01) is True
    with session() as db:
        job = db.query(IngestJob).filter_by(organization_id="org-decagon").one()
        job.status = "running"
        db.commit()
    assert _wait_for_ingest("org-decagon", timeout=0.05, interval=0.01) is False
    with session() as db:
        db.query(IngestJob).filter_by(organization_id="org-decagon").one().status = "failed"
        db.commit()
    assert _wait_for_ingest("org-decagon", timeout=1, interval=0.01) is False
