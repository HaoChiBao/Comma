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


AMENDMENT = "Contract Amendment - Revised contract pricing - 2025-09-20 - $4,600.00"
INVOICE = "Vendor Invoice - Sample Data Services - Observability - 2025-09-27 - $4,600.00"
PURCHASE = "Purchase Order - Sample Data Services - Data annotation - 2025-09-12 - $4,200.00"


def test_summary_names_a_document_by_its_id_when_the_title_is_long():
    graph = {
        "nodes": [
            {
                "id": "artifact:amd",
                "label": AMENDMENT,
                "document_id": "AMD-001",
                "type": "CONTRACT",
                "amount": "4600.00",
                "case_id": "C03",
                "text": "Pricing is revised for the remaining term.",
                "redacted": False,
            },
            {
                "id": "artifact:inv",
                "label": INVOICE,
                "document_id": "INV-C03-381",
                "type": "INVOICE",
                "amount": "4800.00",
                "case_id": "C03",
                "text": "Invoice for September observability.",
                "redacted": False,
            },
            {
                "id": "artifact:po",
                "label": PURCHASE,
                "document_id": "PO-C03-1123",
                "type": "PURCHASE_ORDER",
                "amount": "4200.00",
                "case_id": "C03",
                "text": "Order for data annotation.",
                "redacted": False,
            },
            {
                "id": "artifact:je",
                "label": "JE-900",
                "document_id": "JE-900",
                "type": "JOURNAL",
                "amount": "4600.00",
                "case_id": "C03",
                "text": "Posted the amended amount.",
                "redacted": False,
            },
            {
                "id": "artifact:workpaper",
                "label": "Expense Accrual Workpaper - C03 2025-09-29 Close Draft v1 - 2025-09-29",
                "document_id": "WB-C03-DRAFT",
                "type": "DOCUMENT",
                "amount": "4200.00",
                "case_id": "C08",
                "text": "Draft accrual that belongs to another case.",
                "redacted": False,
            },
        ],
        "edges": [
            {"source": "artifact:amd", "target": "artifact:inv", "type": "CONTRADICTS", "explanation": "Amounts differ."},
            {"source": "artifact:amd", "target": "artifact:po", "type": "CITES", "explanation": "Amendment cites the order."},
            {"source": "artifact:amd", "target": "artifact:je", "type": "CITES", "explanation": "Amendment cites the journal."},
            {"source": "artifact:amd", "target": "artifact:workpaper", "type": "CONTRADICTS", "explanation": "Different case."},
        ],
    }
    summary = summarize_node(graph, "artifact:amd")
    assert summary is not None
    assert summary["what"] == "AMD-001 is a contract for $4600.00. It says: Pricing is revised for the remaining term."
    assert summary["relationships"] == "Other cases also contradict AMD-001."
    assert "INV-C03-381 is an invoice for $4800.00" in summary["connected_to"]
    assert "PO-C03-1123 is a purchase order for $4200.00" in summary["connected_to"]
    assert "JE-900 is a journal entry for $4600.00" in summary["connected_to"]
    assert summary["risks"] == "The amounts disagree."
    joined = summary["summary"]
    for title in (AMENDMENT, INVOICE, PURCHASE, "Expense Accrual Workpaper"):
        assert title not in joined
    assert "WB-C03-DRAFT" not in joined
    assert "Draft accrual" not in joined
    assert "_cited_ids" not in summary
    assert "_titles" not in summary


def test_an_unexplained_record_is_named_by_its_id():
    long_name = "Accrual/Reversal Event - Sample Data Services - Accrual for Sample Data Services"
    graph = {
        "nodes": [
            {
                "id": "artifact:acr",
                "label": long_name,
                "document_id": "ACR-C03-202509",
                "type": "DECISION",
                "amount": "4200.00",
                "origin": "unexplained",
                "redacted": False,
            }
        ],
        "edges": [],
    }
    summary = summarize_node(graph, "artifact:acr")
    assert summary is not None
    assert summary["what"] == "ACR-C03-202509 is a recorded decision for $4200.00."
    assert summary["risks"] == "No earlier visible amount explains ACR-C03-202509."
    assert long_name not in summary["summary"]


def test_a_model_that_restores_a_long_title_is_discarded(monkeypatch):
    invoice = "Vendor Invoice - Sample Data Services - Observability - 2025-09-27 - $4,600.00"
    brief = {
        "label": AMENDMENT,
        "what": "AMD-001 is a contract for $4600.00.",
        "relationships": "Other cases also contradict AMD-001.",
        "connected_to": "INV-C03-381 is an invoice for $4800.00. WB-C03-DRAFT is a document for $4200.00.",
        "risks": "The amounts disagree.",
        "_cited_ids": ["AMD-001", "INV-C03-381", "WB-C03-DRAFT"],
        "_titles": [AMENDMENT, invoice],
    }
    sent = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                f"{AMENDMENT} is a contract for $4600.00. {invoice} is an invoice for $4800.00. "
                                "The amounts disagree."
                            )
                        }
                    }
                ]
            }

    def post(*args, **kwargs):
        sent["body"] = kwargs["json"]
        return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", post)
    text = narrate(brief)
    assert "longer name" in sent["body"]["messages"][0]["content"]
    assert text.startswith("AMD-001 is a contract for $4600.00.")
    assert AMENDMENT not in text
    assert invoice not in text
    assert "WB-C03-DRAFT" in text
    assert "INV-C03-381" in text


def test_a_model_rewrite_keeps_document_ids(monkeypatch):
    brief = {
        "label": AMENDMENT,
        "what": "AMD-001 is a contract for $4600.00.",
        "relationships": "",
        "connected_to": "INV-C03-381 is an invoice for $4800.00.",
        "risks": "The amounts disagree.",
        "_cited_ids": ["AMD-001", "INV-C03-381"],
        "_titles": [INVOICE],
    }
    rewritten = "AMD-001 is a contract for $4600.00. INV-C03-381 is an invoice for $4800.00. The amounts disagree."

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": rewritten}}]}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    assert narrate(brief) == rewritten


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
