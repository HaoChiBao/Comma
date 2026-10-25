from fastapi.testclient import TestClient

from app.node_summary import narrate, summarize_node


def test_summary_uses_only_the_visible_graph():
    graph = {
        "nodes": [
            {
                "id": "artifact:po",
                "label": "PO-101",
                "type": "PURCHASE_ORDER",
                "amount": "145.00",
                "available_from": "2025-09-12T10:00:00+00:00",
                "text": "Purchase order for cloud usage.",
                "origin": "sourced",
                "redacted": False,
            },
            {
                "id": "artifact:msg",
                "label": "MSG-0001",
                "type": "EMAIL",
                "amount": "160.00",
                "text": "Revised September estimate for cloud usage.",
                "redacted": False,
            },
        ],
        "edges": [
            {
                "source": "artifact:po",
                "target": "artifact:msg",
                "type": "CONTRADICTS",
                "explanation": "These documents name the same transaction and the amounts differ.",
            }
        ],
    }
    summary = summarize_node(graph, "artifact:po")
    assert summary is not None
    text = summary["summary"]
    assert "PO-101" in text
    assert "purchase order" in text
    assert "145.00" in text
    assert "MSG-0001" in text
    assert "Revised September estimate" in text
    assert "160.00" in text
    assert "disagree" in text
    assert "158.00" not in text
    assert summarize_node(graph, "artifact:hidden") is None


def test_a_contradiction_from_another_case_is_not_read_as_this_structure():
    graph = {
        "nodes": [
            {
                "id": "artifact:po",
                "label": "PO-101",
                "type": "PURCHASE_ORDER",
                "amount": "145.00",
                "case_id": "C01",
                "text": "Order for cloud usage.",
                "redacted": False,
            },
            {
                "id": "artifact:other",
                "label": "JE-999",
                "type": "JOURNAL",
                "amount": "700.00",
                "case_id": "C08",
                "text": "Unrelated accrual note.",
                "redacted": False,
            },
        ],
        "edges": [
            {
                "source": "artifact:po",
                "target": "artifact:other",
                "type": "CONTRADICTS",
                "explanation": "These documents refer to the same record and the amounts differ.",
            }
        ],
    }
    summary = summarize_node(graph, "artifact:po")
    assert summary is not None
    assert "Unrelated accrual note" not in summary["summary"]
    assert "700.00" not in summary["summary"]
    assert "Other cases" in summary["relationships"]


def test_a_model_that_adds_an_amount_is_discarded(monkeypatch):
    brief = {
        "what": "PO-101 is a purchase order. The recorded amount is $145.00.",
        "relationships": "Its visible relationships are 1 cites.",
        "connected_to": "It connects to MSG-0001 (cites).",
        "risks": "No conflicting amount or unsettled origin is visible from this node.",
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "The invoice INV-381 for $158.00 explains PO-101."}}]}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    text = narrate(brief)
    assert "158.00" not in text
    assert "INV-381" not in text
    assert "145.00" in text


def test_september_summary_does_not_describe_the_december_invoice():
    from app.db import session
    from app.main import create_app
    from app.seed import seed

    app = create_app("sqlite:///:memory:")
    with session() as db:
        seed(db)
    api = TestClient(app)
    params = {"organization_id": "org-acme", "actor_id": "actor-maya", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit"}
    graph = api.get("/api/v1/company-graph", params=params)
    assert graph.status_code == 200, graph.text
    purchase = next(node for node in graph.json()["nodes"] if node["label"] == "PO-101")
    summary = api.get("/api/v1/company-graph/summary", params={**params, "node_id": purchase["id"]})
    assert summary.status_code == 200, summary.text
    assert "158" not in summary.text
    hidden = api.get("/api/v1/company-graph/summary", params={**params, "node_id": "artifact:INV-381:v1"})
    assert hidden.status_code == 404
    assert "158" not in hidden.text
