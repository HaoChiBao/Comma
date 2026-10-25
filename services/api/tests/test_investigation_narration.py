from fastapi.testclient import TestClient

from app.node_summary import narrate_review, review_in_view


def _graph():
    return {
        "nodes": [
            {"id": "artifact:je", "label": "JE-900", "amount": "150.00", "case_id": "CASE-001", "redacted": False},
            {"id": "artifact:po", "label": "PO-101", "amount": "145.00", "case_id": "CASE-001", "redacted": False},
            {"id": "artifact:em", "label": "EM-207", "amount": "160.00", "case_id": "CASE-001", "redacted": False},
            {
                "id": "artifact:hidden",
                "label": "INV-381",
                "amount": "158.00",
                "case_id": "CASE-001",
                "redacted": True,
            },
        ],
        "cases": [{"id": "CASE-001", "vendor": "Cloud Services Inc."}],
    }


def _brief():
    return {
        "thinking": "JE-900 records $150.00. Two earlier amounts in CASE-001 do not match it. They run from $145.00 to $160.00.",
        "action": "Compared 2 records already visible with JE-900.",
        "lines": ["PO-101 · $145.00 · 12 Sep 2025", "EM-207 · $160.00 · 25 Sep 2025"],
        "verdict": "PO-101 is $5.00 lower. EM-207 is $10.00 higher.",
    }


def test_a_review_may_use_a_gap_between_visible_amounts():
    text = _brief()["thinking"] + " " + _brief()["verdict"]
    assert review_in_view(_graph(), text)


def test_a_review_may_not_name_a_withheld_amount_or_record():
    graph = _graph()
    assert not review_in_view(graph, "INV-381 records $158.00.")
    assert not review_in_view(graph, "The later invoice is $158.00.")


def test_a_model_review_is_kept_when_it_stays_inside_the_brief(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "THINKING\n"
                                "JE-900 is booked at $150.00, and the earlier CASE-001 amounts run from $145.00 to $160.00.\n"
                                "ACTION\n"
                                "The comparison used the two records already visible beside JE-900.\n"
                                "VERDICT\n"
                                "PO-101 is $5.00 lower and EM-207 is $10.00 higher.\n"
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    parsed = narrate_review(_brief())
    assert parsed is not None
    assert "JE-900" in parsed["THINKING"]
    assert "$150.00" in parsed["THINKING"]


def test_a_model_review_that_adds_a_record_is_discarded(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "THINKING\nINV-381 for $158.00 explains the accrual.\n"
                                "ACTION\nCompared the invoice.\n"
                                "VERDICT\nThe source is the December invoice.\n"
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    assert narrate_review(_brief()) is None


def test_a_model_review_that_adds_a_date_or_score_is_discarded(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "THINKING\nJE-900 records $150.00.\n"
                                "ACTION\nCompared PO-101 and EM-207.\n"
                                "VERDICT\nThis is 94% certain as of 1 Mar 2026.\n"
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    assert narrate_review(_brief()) is None


def test_investigate_rejects_a_hidden_amount_without_calling_the_model(monkeypatch):
    from app.main import create_app

    calls = {"count": 0}

    def refuse(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("the model must not see a withheld amount")

    monkeypatch.setattr("app.company_graph.company_graph", lambda *args, **kwargs: _graph())
    monkeypatch.setattr("app.node_summary.httpx.post", refuse)
    app = create_app("sqlite:///:memory:")
    api = TestClient(app)
    response = api.post(
        "/api/v1/company-graph/investigate",
        params={"organization_id": "org-acme", "actor_id": "actor-maya", "node_id": "artifact:je"},
        json={
            "thinking": "INV-381 records $158.00.",
            "action": "Compared the hidden invoice.",
            "lines": [],
            "verdict": "The December invoice explains it.",
        },
    )
    assert response.status_code == 422
    assert "158" not in response.text
    assert "INV-381" not in response.text
    assert calls["count"] == 0


def test_investigate_returns_the_model_wording_and_keeps_the_record_lines(monkeypatch):
    from app.main import create_app

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "THINKING\n"
                                "JE-900 is $150.00. CASE-001 has earlier amounts from $145.00 to $160.00.\n"
                                "ACTION\n"
                                "Two visible records were set beside JE-900.\n"
                                "VERDICT\n"
                                "PO-101 is $5.00 lower. EM-207 is $10.00 higher.\n"
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.company_graph.company_graph", lambda *args, **kwargs: _graph())
    monkeypatch.setattr("app.node_summary.httpx.post", lambda *args, **kwargs: Response())
    app = create_app("sqlite:///:memory:")
    api = TestClient(app)
    response = api.post(
        "/api/v1/company-graph/investigate",
        params={"organization_id": "org-acme", "actor_id": "actor-maya", "node_id": "artifact:je"},
        json=_brief(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["narrated"] is True
    assert body["steps"][1]["lines"] == _brief()["lines"]
    assert "Two visible records" in body["steps"][1]["text"]
    assert "158" not in response.text
