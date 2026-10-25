from fastapi.testclient import TestClient

from app.main import create_app

SEPT = "2025-09-30T23:59:00+00:00"
DEC = "2025-12-15T12:00:00+00:00"
OCT = "2025-10-03T00:00:00+00:00"
MAYA = "actor-maya"
JORDAN = "actor-jordan"
RILEY = "actor-riley"


def client():
    app = create_app("sqlite:///:memory:")
    from app.db import session
    from app.seed import seed

    with session() as db:
        seed(db)
    return TestClient(app)


def snap(api, case_id, actor, as_of, mode="audit"):
    response = api.post("/api/v1/snapshots", json={"case_id": case_id, "actor_id": actor, "as_of": as_of, "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_seeded_demo_runs_offline_without_llm_key():
    api = client()
    assert api.get("/health").json()["status"] == "ok"
    cases = api.get("/api/v1/cases").json()
    assert len(cases) >= 8
    snapshot = snap(api, "CASE-001", MAYA, SEPT)
    assert snapshot["allowed_count"] >= 40
    investigation = api.post("/api/v1/investigations", json={"snapshot_id": snapshot["id"]})
    assert investigation.status_code == 200
    assert investigation.json()["finding"]["finding"] in {"QUESTIONABLE", "SUPPORTED", "INSUFFICIENT_EVIDENCE", "LIKELY_ERROR"}


def test_september_snapshot_excludes_december_invoice_even_if_invoice_document_date_is_september():
    allowed = snap(client(), "CASE-001", MAYA, SEPT)["allowed_source_version_ids"]
    assert "INV-381:v1" not in allowed
    assert "PO-101:v1" in allowed
    assert "EM-207:v1" in allowed
    assert "JE-900:v1" in allowed


def test_december_snapshot_includes_invoice():
    allowed = snap(client(), "CASE-001", JORDAN, DEC)["allowed_source_version_ids"]
    assert "INV-381:v1" in allowed


def test_snapshot_returns_historical_po_version():
    september = snap(client(), "CASE-002", MAYA, SEPT)["allowed_source_version_ids"]
    october = snap(client(), "CASE-002", MAYA, OCT)["allowed_source_version_ids"]
    assert "PO-220:v1" in september
    assert "PO-220:v2" not in september
    assert "PO-220:v2" in october
    assert "PO-220:v1" not in october


def test_backfilled_ingestion_does_not_change_verified_historical_availability():
    allowed = snap(client(), "CASE-007", MAYA, SEPT)["allowed_source_version_ids"]
    assert "PO-440:v1" in allowed


def test_unknown_availability_not_silently_allowed():
    allowed = snap(client(), "CASE-007", MAYA, SEPT)["allowed_source_version_ids"]
    assert "UNK-441:v1" not in allowed


def test_role_based_historical_acl():
    accountant = snap(client(), "CASE-003", MAYA, SEPT)["allowed_source_version_ids"]
    auditor = snap(client(), "CASE-003", JORDAN, SEPT)["allowed_source_version_ids"]
    assert "CT-010:v1" not in accountant
    assert "CT-010:v1" in auditor


def test_cross_org_access_denied():
    response = client().post("/api/v1/snapshots", json={"case_id": "CASE-001", "actor_id": RILEY, "as_of": SEPT, "mode": "audit"})
    assert response.status_code == 403
    assert response.json()["code"] == "OUTSIDE_ORGANIZATION"
    assert "158" not in response.text


def test_direct_document_lookup_cannot_bypass_snapshot():
    api = client()
    snapshot = snap(api, "CASE-001", MAYA, SEPT)
    response = api.get(f"/api/v1/snapshots/{snapshot['id']}/documents/INV-381:v1")
    assert response.status_code == 403
    assert response.json()["code"] == "FUTURE_INFORMATION"
    assert "158" not in response.text
    assert "INV-381" not in response.text


def test_semantic_and_keyword_search_do_not_leak_blocked_content():
    api = client()
    snapshot = snap(api, "CASE-001", MAYA, SEPT)
    response = api.post(f"/api/v1/snapshots/{snapshot['id']}/search", json={"query": "158"})
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert "158" not in response.text


def test_graph_agent_view_contains_no_future_amount_or_content():
    api = client()
    snapshot = snap(api, "CASE-001", MAYA, SEPT)
    graph = api.get(f"/api/v1/snapshots/{snapshot['id']}/graph?view=agent")
    assert graph.status_code == 200
    body = graph.text
    assert "158" not in body
    assert "INV-381" not in body
    assert "145.00" in body
    assert "160.00" in body
    assert "150.00" in body


def test_auditor_view_marks_future_evidence_separately():
    api = client()
    snapshot = snap(api, "CASE-001", JORDAN, SEPT)
    denied = api.get(f"/api/v1/snapshots/{snap(api, 'CASE-001', MAYA, SEPT)['id']}/graph?view=auditor")
    assert denied.status_code == 403
    graph = api.get(f"/api/v1/snapshots/{snapshot['id']}/graph?view=auditor").json()
    redacted = [node for node in graph["nodes"] if node["redacted"]]
    assert redacted
    assert all(node["amount"] is None for node in redacted)
    assert "158.00" not in api.get(f"/api/v1/snapshots/{snapshot['id']}/graph?view=auditor").text


def test_predecision_mode_hides_recorded_outcome():
    allowed = snap(client(), "CASE-010", MAYA, SEPT, "pre_decision")["allowed_source_version_ids"]
    assert "JE-910:v1" not in allowed
    assert "PO-610:v1" in allowed


def test_no_false_derived_from_edge_without_calculation():
    api = client()
    graph = api.get(f"/api/v1/snapshots/{snap(api, 'CASE-001', MAYA, SEPT)['id']}/graph?view=agent").json()
    assert all(edge["type"] != "DERIVED_FROM" for edge in graph["edges"])
    calculated = api.get(f"/api/v1/snapshots/{snap(api, 'CASE-002', MAYA, SEPT)['id']}/graph?view=agent").json()
    derived = [edge for edge in calculated["edges"] if edge["type"] == "DERIVED_FROM"]
    assert derived
    assert all("JE-901" not in edge["source"] and "JE-901" not in edge["target"] for edge in derived)


def test_every_reported_amount_has_source_or_explicitly_unverified_label():
    api = client()
    finding = api.post("/api/v1/investigations", json={"snapshot_id": snap(api, "CASE-001", MAYA, SEPT)["id"]}).json()["finding"]
    assert finding["observations"]
    assert all(item["source_version_id"] for item in finding["observations"])
    assert any("unexplained" in item.lower() or "formula" in item.lower() for item in finding["uncertainties"])


def test_agent_attempt_to_read_future_invoice_is_denied_and_logged():
    api = client()
    detail = api.post("/api/v1/investigations", json={"snapshot_id": snap(api, "CASE-001", MAYA, SEPT)["id"]}).json()
    blocked = [event for event in detail["tool_events"] if event["blocked_reason"]]
    assert any(event["params"]["source_version_id"] == "INV-381:v1" and event["blocked_reason"] == "FUTURE_INFORMATION" for event in blocked)
    assert "158" not in str(detail["finding"])


def test_only_authorized_human_can_approve_proposal():
    api = client()
    detail = api.post("/api/v1/investigations", json={"snapshot_id": snap(api, "CASE-001", JORDAN, SEPT)["id"]}).json()
    proposal_id = detail["proposal"]["id"]
    denied = api.post(f"/api/v1/proposals/{proposal_id}/review", json={"actor_id": MAYA, "decision": "approve"})
    assert denied.status_code == 403
    approved = api.post(f"/api/v1/proposals/{proposal_id}/review", json={"actor_id": JORDAN, "decision": "approve", "comment": "Reviewed."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_identical_snapshot_has_identical_manifest_hash():
    api = client()
    first = snap(api, "CASE-001", MAYA, SEPT)
    second = snap(api, "CASE-001", MAYA, SEPT)
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["id"] == second["id"]


def test_policy_effective_later_does_not_govern_september():
    api = client()
    graph = api.get(f"/api/v1/snapshots/{snap(api, 'CASE-006', JORDAN, DEC)['id']}/graph?view=auditor").json()
    governed = [edge for edge in graph["edges"] if edge["type"] == "GOVERNED_BY"]
    assert any("POL-ACCRUAL:v1" in edge["source"] for edge in governed)
    assert all("POL-ACCRUAL:v2" not in edge["source"] for edge in governed)


def test_injection_text_does_not_open_future_invoice():
    api = client()
    snapshot = snap(api, "CASE-009", MAYA, SEPT)
    assert "INV-381:v1" not in snapshot["allowed_source_version_ids"]
    memo = api.post(f"/api/v1/snapshots/{snapshot['id']}/search", json={"query": "ignore time restrictions"})
    assert memo.status_code == 200
    assert memo.json()["results"]
    assert "158" not in memo.text


def test_multi_entity_amounts_are_not_linked():
    api = client()
    graph = api.get(f"/api/v1/snapshots/{snap(api, 'CASE-008', MAYA, SEPT)['id']}/graph?view=agent").json()
    pairs = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    labels = {node["id"]: node for node in graph["nodes"]}
    for source, target in pairs:
        left = labels.get(source, {})
        right = labels.get(target, {})
        if left.get("amount") == "700.00" or right.get("amount") == "700.00":
            assert edge_type(graph, source, target) != "CONTRADICTS"


def edge_type(graph, source, target):
    for edge in graph["edges"]:
        if edge["source"] == source and edge["target"] == target:
            return edge["type"]
    return None


def test_compare_shows_unrestricted_leakage_and_constrained_does_not():
    api = client()
    body = api.post(
        "/api/v1/evaluations/compare",
        json={"case_id": "CASE-001", "actor_id": MAYA, "as_of": SEPT, "mode": "audit"},
    ).json()
    assert body["unrestricted"]["unsafe"] is True
    assert "INV-381:v1" in body["leakage"]
    assert "158.00" in body["unrestricted"]["summary"]
    assert "158" not in str(body["constrained"]["finding"])
