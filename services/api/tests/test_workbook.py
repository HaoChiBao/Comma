import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.seed_data import WORKBOOK_ROWS

TRUTH = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "truth.json").read_text())
SEPT = TRUTH["as_of"]["september"]
DEC = TRUTH["as_of"]["december"]
WORKBOOK = TRUTH["workbook_id"]
SHEET = TRUTH["sheet_name"]


def client():
    app = create_app("sqlite:///:memory:")
    from app.db import session
    from app.seed import seed

    with session() as db:
        seed(db)
    return TestClient(app)


def inspect(api, address, raw_value, as_of=SEPT, formula="", precedents=(), **extra):
    body = {
        "workbook_id": WORKBOOK,
        "sheet_name": SHEET,
        "cell_address": address,
        "raw_value": raw_value,
        "display_text": f"${raw_value:,.2f}" if isinstance(raw_value, (int, float)) else str(raw_value),
        "formula": formula,
        "direct_precedents": list(precedents),
        "as_of": as_of,
        **extra,
    }
    return api.post("/v1/traces/inspect", json=body)


def inspect_d3(api, as_of=SEPT):
    response = inspect(api, "D3", 150, as_of, "=B3+C3", ["Accruals!B3", "Accruals!C3"], precedent_values={"Accruals!B3": 100, "Accruals!C3": 50})
    assert response.status_code == 200, response.text
    return response


def sheet_values():
    """What the add-in reads from the demo workbook: rows 1-11, columns B-D."""
    cells = []
    for row, _vendor, _entity, line_1, line_2, manual, _ref in WORKBOOK_ROWS:
        if line_1 is None:
            cells.append({"address": f"D{row}", "raw_value": float(manual)})
            continue
        cells.append({"address": f"B{row}", "raw_value": float(line_1)})
        cells.append({"address": f"C{row}", "raw_value": float(line_2)})
        cells.append({"address": f"D{row}", "raw_value": float(Decimal(line_1) + Decimal(line_2)), "formula": f"=B{row}+C{row}"})
    cells.append({"address": "A1", "raw_value": "Vendor"})
    return cells


def test_d3_september_is_review_against_vendor_email():
    body = inspect_d3(client()).json()
    assert body["status"] == "review"
    assert body["title"] == "Potential estimate discrepancy"
    assert body["difference"] == {"amount": "10.00", "currency": "USD"}
    assert "EM-208" in body["summary"] and "may be reasonable" in body["summary"].lower()
    assert body["lineage"] == [
        {"from": "Accruals!B3", "to": "Accruals!D3", "kind": "FORMULA_DEPENDS_ON", "verified": True},
        {"from": "Accruals!C3", "to": "Accruals!D3", "kind": "FORMULA_DEPENDS_ON", "verified": True},
    ]
    email = next(item for item in body["evidence"] if item["source_id"] == "EM-208:v1")
    assert email["relationship"] == "CONFLICTS_WITH"
    assert email["link_status"] == "candidate"
    assert email["amount"] == "160.00"
    assert email["locator"] == "message body, paragraph 2"
    po_lines = [item for item in body["evidence"] if item["source_id"] == "PO-104:v1" and item["cell"] != "Accruals!D3"]
    assert {(item["cell"], item["amount"], item["comparison"]) for item in po_lines} == {
        ("Accruals!B3", "100.00", "match"),
        ("Accruals!C3", "50.00", "match"),
    }
    assert any("No proof that the preparer read" in item for item in body["unknowns"])


def without_ids(body: dict) -> str:
    """Random ids and hashes are hex and can contain '158' by chance."""
    return json.dumps({key: value for key, value in body.items() if key not in {"trace_id", "snapshot_id", "manifest_hash", "request_id"}})


def test_september_trace_withholds_all_invoice_content():
    api = client()
    body = inspect_d3(api).json()
    invoice = [item for item in body["evidence"] if item["source_id"] == "INV-104:v1"]
    assert invoice == [
        {"cell": "Accruals!D3", "source_id": "INV-104:v1", "status": "excluded", "reason": "FUTURE_INFORMATION", "relationship": "POSSIBLY_RELATED_TO", "link_status": "candidate"}
    ]
    text = without_ids(body)
    for leaked in ("158", "Amount due", "Received by accounting on 10 December", "2025-12-10"):
        assert leaked not in text
    graph = api.get(f"/v1/traces/{body['trace_id']}/graph")
    assert graph.status_code == 200
    assert "158" not in without_ids(graph.json())
    blocked = [node for node in graph.json()["nodes"] if node["type"] == "Blocked"]
    assert blocked and all(node["amount"] is None and node["source_id"] is None for node in blocked)


def test_direct_source_request_is_denied_under_september_trace():
    api = client()
    trace_id = inspect_d3(api).json()["trace_id"]
    denied = api.get(f"/v1/sources/INV-104:v1?trace_id={trace_id}")
    assert denied.status_code == 403
    assert denied.json()["code"] == "FUTURE_INFORMATION"
    assert denied.json()["request_id"]
    assert "158" not in without_ids(denied.json())
    allowed = api.get(f"/v1/sources/EM-208:v1?trace_id={trace_id}")
    assert allowed.status_code == 200
    assert allowed.json()["facts"][0]["locator"] == "message body, paragraph 2"


def test_december_trace_exposes_invoice():
    api = client()
    body = inspect_d3(api, DEC).json()
    invoice = next(item for item in body["evidence"] if item["source_id"] == "INV-104:v1")
    assert invoice["status"] == "available"
    assert invoice["amount"] == "158.00"
    assert invoice["relationship"] == "CONFLICTS_WITH"
    assert invoice["document_date"].startswith("2025-09-29")
    assert invoice["available_at"].startswith("2025-12-10")
    source = api.get(f"/v1/sources/INV-104:v1?trace_id={body['trace_id']}")
    assert source.status_code == 200
    assert "158.00" in source.text


def test_manual_cell_is_unknown_without_invented_lineage():
    body = inspect(client(), "D5", 980).json()
    assert body["status"] == "unknown"
    assert body["title"] == "Unknown source"
    assert body["lineage"] == []
    assert body["evidence"] == []
    assert "No preserved source explains this amount." in body["unknowns"]


def test_controls_are_supported():
    api = client()
    for address, value in (("D2", 80), ("D4", 200), ("D10", 310), ("D11", 125)):
        body = inspect(api, address, value).json()
        assert body["status"] == "supported", (address, body)


def test_cross_currency_and_cross_period_amounts_are_not_flagged():
    api = client()
    euro = inspect(api, "D7", 200).json()
    assert euro["status"] == "supported"
    quote = next(item for item in euro["evidence"] if item["source_id"] == "EM-451:v1")
    assert quote["comparison"] == "not_comparable_currency"
    assert quote["relationship"] == "POSSIBLY_RELATED_TO"
    october = inspect(api, "D8", 90).json()
    assert october["status"] == "supported"
    assert next(item for item in october["evidence"] if item["source_id"] == "EM-461:v1")["comparison"] == "not_comparable_period"


def test_tolerance_edges():
    api = client()
    within = inspect(api, "D9", 250).json()
    assert within["status"] == "supported"
    assert next(item for item in within["evidence"] if item["source_id"] == "EM-471:v1")["comparison"] == "within_tolerance"
    # $252 vs $249.40 is 2.60 apart, beyond max($1, 1% of 249.40).
    beyond = inspect(api, "D9", 249.40).json()
    assert beyond["status"] == "review"


def test_reference_to_another_vendors_po_is_review():
    body = inspect(client(), "D6", 45).json()
    assert body["status"] == "review"
    assert body["title"] == "Reference points to a different vendor"
    assert "D3" in body["summary"]


def test_changed_cell_value_is_compared_live():
    body = inspect(client(), "D2", 95).json()
    assert body["status"] == "review"
    assert body["difference"]["amount"] == "15.00"


def test_scan_matches_ground_truth():
    api = client()
    for as_of in (SEPT, DEC):
        response = api.post("/v1/workbooks/scan", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "range_address": TRUTH["scan_range"], "cells": sheet_values(), "as_of": as_of})
        assert response.status_code == 200, response.text
        flagged = {item["address"]: item["status"] for item in response.json()["findings"] if item["status"] != "supported"}
        assert flagged == {address: flag["status"] for address, flag in TRUTH["flags"].items()}
        statuses = {item["address"]: item["status"] for item in response.json()["findings"]}
        assert all(statuses[address] == "supported" for address in TRUTH["controls"])


def test_errors_are_typed():
    api = client()
    bad = inspect(api, "A1:B2", 1)
    assert bad.status_code == 400
    assert bad.json()["code"] == "INVALID_CELL"
    assert bad.json()["request_id"]
    missing = api.get("/v1/traces/trc_missing/graph")
    assert missing.status_code == 404
    assert missing.json()["code"] == "TRACE_NOT_FOUND"
    malformed = api.post("/v1/traces/inspect", json={"workbook_id": WORKBOOK})
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "INVALID_REQUEST"
    unknown_book = api.post("/v1/traces/inspect", json={"workbook_id": "nope", "sheet_name": SHEET, "cell_address": "D3", "as_of": SEPT})
    assert unknown_book.status_code == 404
    too_many = api.post("/v1/workbooks/scan", json={"workbook_id": WORKBOOK, "sheet_name": SHEET, "cells": [{"address": "A1"}] * 201, "as_of": SEPT})
    assert too_many.status_code == 400
    assert too_many.json()["code"] == "RANGE_TOO_LARGE"


def test_other_organization_cannot_trace_workbook():
    response = inspect(client(), "D3", 150, actor_id="actor-riley")
    assert response.status_code == 403
    assert response.json()["code"] == "OUTSIDE_ORGANIZATION"


def test_workbook_seed_is_added_to_an_existing_database():
    app = create_app("sqlite:///:memory:")
    from app.db import session
    from app.models import Case, WorkbookCell
    from app.seed import _seed_world, seed

    with session() as db:
        _seed_world(db)
        assert db.get(Case, "CASE-011") is None
        seed(db)
        seed(db)
        assert db.query(WorkbookCell).filter(WorkbookCell.address == "D3").count() == 1
    assert TestClient(app).get("/health").status_code == 200
