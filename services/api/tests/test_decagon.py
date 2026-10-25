import json
import os

from fastapi.testclient import TestClient

os.environ["INGEST_INLINE"] = "1"


def client():
    from app.main import create_app
    from app.seed import seed
    from app.db import session

    app = create_app("sqlite:///:memory:")
    with session() as db:
        seed(db)
    return TestClient(app)


def test_decagon_analysis_hides_the_invoice_until_december():
    api = client()
    started = api.post("/api/v1/ingestions/decagon")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "completed"
    september = api.get(
        "/api/v1/company-graph",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit", "view": "agent"},
    )
    assert september.status_code == 200, september.text
    body = september.json()
    amounts = {node["amount"] for node in body["nodes"] if node["case_id"] == "C01"}
    assert "145.00" in amounts
    assert "160.00" in amounts
    assert "150.00" in amounts
    assert "158.00" not in amounts
    assert "158.00" not in json.dumps(body)
    before = api.get(
        "/api/v1/company-graph",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-09-30T16:59:59+00:00", "mode": "audit", "view": "agent"},
    )
    early = {node["amount"] for node in before.json()["nodes"] if node["case_id"] == "C01"}
    assert "150.00" not in early
    assert "158.00" not in early
    december = api.get(
        "/api/v1/company-graph",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-12-15T12:00:00+00:00", "mode": "audit", "view": "agent"},
    )
    later = december.json()
    c01 = next(item for item in later["cases"] if item["id"] == "C01")
    assert "158.00" in {node["amount"] for node in later["nodes"] if node["case_id"] == "C01"}
    assert "8.00" in c01["finding"]
    assert later["corpus"]["documents"] >= 500
    assert len(later["nodes"]) >= 400
    assert sum(edge["type"] == "MENTIONS" for edge in later["edges"]) >= 20
    assert any(edge["type"] == "CONTRADICTS" for edge in later["edges"])
    assert all(not edge["id"].startswith("ge-") for edge in later["edges"])
    legal = api.get(
        "/api/v1/company-graph",
        params={"actor_id": "actor-decagon-legal", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit", "view": "agent"},
    )
    finance_ids = {node.get("document_id") or node["label"] for node in body["nodes"]}
    legal_ids = {node.get("document_id") or node["label"] for node in legal.json()["nodes"]}
    assert "CTR-0007" in legal_ids
    assert "CTR-0007" not in finance_ids
    invoice = next(node for node in later["nodes"] if (node.get("document_id") or node["label"]) == "INV-C01-381")
    assert invoice["label"] != "INV-C01-381"
    assert invoice["title"] == invoice["label"]
    visible = api.get(
        f"/api/v1/company-graph/documents/{invoice['version_id']}",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-12-15T12:00:00+00:00", "mode": "audit"},
    )
    assert visible.status_code == 200, visible.text
    assert "158.00" in visible.json()["text"]
    hidden = api.get(
        f"/api/v1/company-graph/documents/{invoice['version_id']}",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit"},
    )
    assert hidden.status_code == 403
    assert "158" not in hidden.text
    restricted = next(node for node in legal.json()["nodes"] if (node.get("document_id") or node["label"]) == "CTR-0007")
    blocked = api.get(
        f"/api/v1/company-graph/documents/{restricted['version_id']}",
        params={"actor_id": "actor-decagon-finance", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit"},
    )
    assert blocked.status_code == 403
    assert "2100" not in blocked.text
