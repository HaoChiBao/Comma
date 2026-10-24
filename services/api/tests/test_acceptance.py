"""API acceptance coverage for spec section 16 and the BOM edge cases."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.bootstrap import sid
from app.clock import clock
from app.db import SessionLocal
from app.fixtures.catalog import DEMO_REQUEST
from app.models import Approval, Message, ProcurementTask, PurchaseOrder, PurchasePlan, Rfq, SupplierQuote
from app.settings import settings
from test_optimizer import WINNING, expected_from_picks

MAYA = "+15555550100"
SAM = "+15555550102"
JORDAN = "+15555550101"
RILEY = "+15555550200"
UNKNOWN = "+15555550999"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, phone: str) -> str:
    response = client.post("/api/auth/login", json={"phone_e164": phone})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _inbound(client, text: str, message_id: str, sender: str = MAYA):
    response = client.post(
        "/dev/inbound-message",
        json={"sender": sender, "text": text, "provider_message_id": message_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _detail(client, token: str, task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _quoting(client, text: str = DEMO_REQUEST, message_id: str = "msg-demo"):
    created = _inbound(client, text, message_id)
    token = _login(client, MAYA)
    detail = _detail(client, token, created["task_id"])
    return token, created, detail


def _pending(client, text: str = DEMO_REQUEST, message_id: str = "msg-demo"):
    token, created, detail = _quoting(client, text, message_id)
    simulated = client.post(
        f"/dev/tasks/{created['task_id']}/simulate-replies",
        headers=_auth(token),
    )
    assert simulated.status_code == 200, simulated.text
    detail = _detail(client, token, created["task_id"])
    return token, created, detail


def _sign_photon(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    digest = hmac.new(
        settings.photon_webhook_secret.encode(),
        f"v0:{stamp}:{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Spectrum-Timestamp": stamp,
        "X-Spectrum-Signature": f"v0={digest}",
        "X-Spectrum-Event": "messages",
        "X-Spectrum-Webhook-Id": "wh-test",
        "Content-Type": "application/json",
    }
    return body, headers


def _photon_payload(message_id: str, sender: str, text: str) -> dict:
    return {
        "event": "messages",
        "space": {"phone": "+15555551000"},
        "message": {
            "id": message_id,
            "timestamp": "2026-10-24T15:00:00Z",
            "sender": {"id": sender},
            "content": {"type": "text", "text": text},
        },
    }


def _sign_email(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(settings.email_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Email-Signature": f"sha256={digest}", "Content-Type": "application/json"}


def _current_plan(detail: dict) -> dict:
    for plan in reversed(detail["plans"]):
        if plan["status"] in ("proposed", "approved", "executed"):
            return plan
    raise AssertionError(detail["plans"])


def test_authorized_and_unauthorized_sender(client):
    denied = _inbound(client, DEMO_REQUEST, "unauth-1", sender=UNKNOWN)
    assert denied["authorized"] is False
    assert denied["task_id"] is None
    session = SessionLocal()
    try:
        outbound = session.scalar(select(Message).where(Message.provider_message_id == "onboarding-unauth-1"))
        text = outbound.normalized_json["text"]
        assert "not enrolled" in text
        assert "BETA-1001" not in text
        assert "ACME-" not in text
        assert outbound.raw_json["delivered_to_network"] is False
    finally:
        session.close()
    allowed = _inbound(client, DEMO_REQUEST, "auth-1")
    assert allowed["authorized"] is True
    assert allowed["public_code"].startswith("ACME-")
    assert allowed["task_id"]


def test_duplicate_photon_webhook(client):
    payload = _photon_payload("photon-dup-1", MAYA, DEMO_REQUEST)
    body, headers = _sign_photon(payload)
    first = client.post("/webhooks/photon", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["deduped"] is False
    second = client.post("/webhooks/photon", content=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["deduped"] is True
    assert second.json()["task_id"] == first.json()["task_id"]
    session = SessionLocal()
    try:
        count = session.scalar(
            select(func.count()).select_from(ProcurementTask).where(ProcurementTask.public_code.like("ACME-%"))
        )
        assert count == 1
    finally:
        session.close()
    bad_body, bad_headers = _sign_photon(payload)
    bad_headers["X-Spectrum-Signature"] = "v0=" + ("ab" * 32)
    rejected = client.post("/webhooks/photon", content=bad_body, headers=bad_headers)
    assert rejected.status_code == 401


def test_ambiguous_message_clarification_loop(client):
    first = _inbound(client, "Need some computers for the lab", "clarify-1")
    token = _login(client, MAYA)
    detail = _detail(client, token, first["task_id"])
    assert detail["task"]["status"] == "NEEDS_INFO"
    assert detail["task"]["quantity"] is None
    texts = [row["text"] or "" for row in detail["messages"]]
    assert any("quantity" in text and "budget" in text for text in texts)
    second = _inbound(client, "100 desktop computers, budget $200000, by 2026-11-15", "clarify-2")
    assert second["task_id"] == first["task_id"]
    detail = _detail(client, token, first["task_id"])
    assert detail["task"]["status"] == "AWAITING_QUOTES"
    assert detail["task"]["quantity"] == 100
    assert detail["task"]["budget_amount"] == "200000.00"
    assert any("company ship-to" in note for note in detail["assumptions"])
    session = SessionLocal()
    try:
        count = session.scalar(
            select(func.count()).select_from(ProcurementTask).where(ProcurementTask.created_by == sid("actor-maya"))
        )
        assert count == 1
    finally:
        session.close()


def test_stale_quote_triggers_rfq_and_sandbox_email(client):
    token, created, detail = _quoting(client)
    assert detail["task"]["status"] == "AWAITING_QUOTES"
    assert len(detail["rfqs"]) >= 8
    reasons = [event["reason"] or "" for event in detail["events"] if event["action"] == "rfq_created"]
    assert any("stale" in reason for reason in reasons)
    assert any("missing" in reason for reason in reasons)
    sandbox = client.get(f"/dev/tasks/{created['task_id']}/sandbox", headers=_auth(token))
    assert sandbox.status_code == 200, sandbox.text
    emails = [row for row in sandbox.json()["sandbox"] if row["kind"] == "email_rfq"]
    assert emails
    assert all(row["delivered_to_network"] is False and row["simulated"] is True for row in emails)
    assert any("RFQ-TOKEN:" in row["payload"]["body"] for row in emails)
    assert all("not delivered" in row["payload"]["body"].lower() or "sandbox" in row["payload"]["body"].lower() for row in emails)


def test_inbound_email_correlation_and_duplicate(client):
    token, created, detail = _quoting(client)
    rfq = detail["rfqs"][0]
    sku = rfq["requested"][0]["sku"]
    body = (
        f"RFQ-TOKEN: {rfq['correlation_token']}\n\n"
        f"SKU: {sku}\nUnit price: 10.00\nCurrency: USD\nAvailable qty: 500\nMOQ: 1\n"
        "Shipping: 1.00\nTaxes: 0.00\nArrival: 2026-11-01\nValid until: 2026-12-01\n"
    )
    payload = {"provider_message_id": "email-1", "subject": "quote", "body": body}
    raw, headers = _sign_email(payload)
    first = client.post("/webhooks/email", content=raw, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["correlated"] is True
    duplicate = client.post("/webhooks/email", content=raw, headers=headers)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["deduped"] is True
    subject_only = {
        "provider_message_id": "email-subject-only",
        "subject": f"RFQ-TOKEN: {rfq['correlation_token']}",
        "body": f"SKU: {sku}\nUnit price: 10.00\nCurrency: USD\nAvailable qty: 10\nMOQ: 1\nShipping: 1.00\nTaxes: 0.00\nArrival: 2026-11-01\nValid until: 2026-12-01\n",
    }
    raw, headers = _sign_email(subject_only)
    missed = client.post("/webhooks/email", content=raw, headers=headers)
    assert missed.status_code == 200, missed.text
    assert missed.json()["correlated"] is False
    unsigned = client.post("/webhooks/email", content=raw, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 401


def test_quote_parser_uncertainty_and_spec_mismatch(client):
    token, created, detail = _quoting(client)
    rfq = detail["rfqs"][0]
    sku = rfq["requested"][0]["sku"]
    uncertain = client.post(
        "/dev/supplier-reply",
        json={
            "provider_message_id": "uncertain-1",
            "rfq_token": rfq["correlation_token"],
            "body": f"SKU: {sku}\nUnit price: 10\nUnit price: 12\nCurrency: USD\n",
        },
    )
    assert uncertain.status_code == 200, uncertain.text
    statuses = {row["sku"]: row["status"] for row in uncertain.json()["quotes"]}
    assert statuses[sku] == "NEEDS_REVIEW"
    mismatch = client.post(
        "/dev/supplier-reply",
        json={
            "provider_message_id": "mismatch-1",
            "rfq_token": rfq["correlation_token"],
            "body": (
                "SKU: NOT-A-REAL-SKU\nUnit price: 10.00\nCurrency: USD\nAvailable qty: 10\nMOQ: 1\n"
                "Shipping: 1.00\nTaxes: 0.00\nArrival: 2026-11-01\nValid until: 2026-12-01\n"
            ),
        },
    )
    assert mismatch.status_code == 200, mismatch.text
    assert mismatch.json()["quotes"][0]["status"] == "REJECTED"
    detail = _detail(client, token, created["task_id"])
    rejected = next(row for row in detail["quotes"] if row["sku"] == "NOT-A-REAL-SKU")
    assert "spec_mismatch" in rejected["uncertainty"]


def test_feasible_full_bom_total_from_fixtures(client):
    _goods, shipping, total = expected_from_picks(WINNING)
    token, created, detail = _pending(client)
    assert detail["task"]["status"] == "AWAITING_APPROVAL"
    plan = _current_plan(detail)
    assert plan["total_landed_cost"] == format(total, "f")
    assert isinstance(plan["total_landed_cost"], str)
    assert plan["readiness_computers"] == 100
    assert Decimal(plan["budget_headroom"]) == Decimal("200000.00") - total
    chosen = {(line["component_type"], line["sku"], line["supplier_name"], line["quantity"]) for line in plan["lines"]}
    assert chosen == set(WINNING)
    charged = sum((Decimal(amount) for amount in plan["shipping_by_supplier"].values()), Decimal("0"))
    assert charged == shipping
    assert len(detail["bom"]["lines"]) == 8
    assert detail["bom"]["readiness_computers"] == 100
    meta = client.get("/api/meta")
    assert meta.json()["live_imessage"] is False
    assert meta.json()["live_email"] is False


def test_infeasible_budget_and_deadline(client):
    token, created, detail = _pending(
        client,
        "Build 100 complete desktop computers for our production run, within a $100 total component procurement budget, delivered by November 15.",
        "budget-1",
    )
    assert detail["task"]["status"] == "BLOCKED"
    assert "budget" in (detail["task"]["blocked_reason"] or "")
    assert detail["orders"] == []
    token, created, detail = _pending(
        client,
        "Build 100 complete desktop computers for our production run, within a $200,000 total component procurement budget, delivered by October 25.",
        "deadline-1",
    )
    assert detail["task"]["status"] == "BLOCKED"
    assert "deadline" in (detail["task"]["blocked_reason"] or "")


def test_quote_revision_invalidates_plan_and_replans_ram(client):
    token, created, detail = _pending(client)
    previous = _current_plan(detail)
    previous_approval = detail["pending_approval"]["id"]
    disrupted = client.post(
        f"/dev/tasks/{created['task_id']}/disrupt-ram",
        headers=_auth(token),
        json={"reduce_by": 50},
    )
    assert disrupted.status_code == 200, disrupted.text
    assert disrupted.json()["previous_qty"] - disrupted.json()["available_qty"] == 50
    detail = _detail(client, token, created["task_id"])
    assert detail["task"]["status"] == "AWAITING_APPROVAL"
    plan = _current_plan(detail)
    assert plan["version"] > previous["version"]
    assert plan["id"] != previous["id"]
    assert detail["pending_approval"]["id"] != previous_approval
    old = next(row for row in detail["approvals"] if row["id"] == previous_approval)
    assert old["status"] == "invalidated"
    ram_qty = sum(line["quantity"] for line in plan["lines"] if line["component_type"] == "ram")
    gpu_qty = sum(line["quantity"] for line in plan["lines"] if line["component_type"] == "gpu")
    assert ram_qty == 200
    assert gpu_qty == 100
    assert any(line["quantity"] == 190 for line in plan["lines"] if line["component_type"] == "ram")
    session = SessionLocal()
    try:
        gpu_stock = session.scalar(
            select(func.sum(SupplierQuote.available_qty)).where(
                SupplierQuote.task_id == uuid.UUID(created["task_id"]),
                SupplierQuote.component_type == "gpu",
                SupplierQuote.status == "VALID",
            )
        )
        assert int(gpu_stock) >= 100
    finally:
        session.close()


def test_approval_wrong_actor_expired_stale_and_over_limit(client):
    token, created, detail = _pending(client, message_id="approval-gates")
    approval_id = detail["pending_approval"]["id"]
    sam = _login(client, SAM)
    wrong = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(sam),
        json={"decision": "approve"},
    )
    assert wrong.status_code == 403, wrong.text
    assert _detail(client, token, created["task_id"])["orders"] == []
    assert _detail(client, token, created["task_id"])["pending_approval"]["status"] == "pending"

    settings.manager_approval_limit = Decimal("1.00")
    try:
        limited = client.post(
            f"/api/approvals/{approval_id}/respond",
            headers=_auth(token),
            json={"decision": "approve"},
        )
        assert limited.status_code == 403, limited.text
        assert "limit" in limited.json()["detail"]
        still = _detail(client, token, created["task_id"])
        assert still["pending_approval"]["status"] == "pending"
        assert still["orders"] == []
    finally:
        settings.manager_approval_limit = Decimal("200000.00")

    session = SessionLocal()
    try:
        approval = session.get(Approval, uuid.UUID(approval_id))
        approval.expires_at = clock.now() - timedelta(hours=1)
        session.commit()
    finally:
        session.close()
    expired = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert expired.status_code == 409, expired.text
    assert _detail(client, token, created["task_id"])["orders"] == []

    token, created, detail = _pending(client, message_id="stale-plan")
    approval_id = detail["pending_approval"]["id"]
    plan_id = detail["pending_approval"]["plan_id"]
    session = SessionLocal()
    try:
        plan = session.get(PurchasePlan, uuid.UUID(plan_id))
        plan.plan_hash = "stale-hash"
        session.commit()
    finally:
        session.close()
    stale = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert stale.status_code == 409, stale.text
    detail = _detail(client, token, created["task_id"])
    assert detail["orders"] == []
    assert next(row["status"] for row in detail["approvals"] if row["id"] == approval_id) == "invalidated"


def test_duplicate_approval_creates_one_po_per_supplier(client):
    token, created, detail = _pending(client, message_id="approve-once")
    approval = detail["pending_approval"]
    plan = _current_plan(detail)
    suppliers = {line["supplier_id"] for line in plan["lines"]}
    decision = client.post(
        "/dev/inbound-message",
        json={
            "sender": MAYA,
            "text": f"APPROVE {approval['token']}",
            "provider_message_id": "approve-msg-1",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["approval"]["idempotent"] is False
    again = client.post(
        f"/api/approvals/{approval['id']}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["idempotent"] is True
    detail = _detail(client, token, created["task_id"])
    assert len(detail["orders"]) == len(suppliers)
    assert len({row["supplier_id"] for row in detail["orders"]}) == len(suppliers)
    assert {row["status"] for row in detail["orders"]} <= {"SENT", "AUTHORIZED", "SENDING", "ACKNOWLEDGED"}
    session = SessionLocal()
    try:
        keys = session.scalars(
            select(PurchaseOrder.idempotency_key).where(PurchaseOrder.plan_id == uuid.UUID(plan["id"]))
        ).all()
        assert len(keys) == len(set(keys)) == len(suppliers)
    finally:
        session.close()


def test_supplier_rejection_reopens_and_logs_reason(client):
    token, created, detail = _pending(client, message_id="reject-po")
    approval = detail["pending_approval"]
    client.post(
        f"/api/approvals/{approval['id']}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    detail = _detail(client, token, created["task_id"])
    po_number = detail["orders"][0]["po_number"]
    rejected = client.post(
        f"/dev/tasks/{created['task_id']}/reject-order",
        headers=_auth(token),
        json={"po_number": po_number, "reason": "supplier cannot honor the lead time"},
    )
    assert rejected.status_code == 200, rejected.text
    detail = _detail(client, token, created["task_id"])
    order = next(row for row in detail["orders"] if row["po_number"] == po_number)
    assert order["status"] == "REJECTED"
    assert detail["task"]["status"] in {"EVALUATING", "BLOCKED", "AWAITING_QUOTES", "SOURCING", "AWAITING_APPROVAL"}
    assert any(
        event["action"] == "purchase_order_rejected" and "lead time" in (event["reason"] or "") and event["evidence_refs"]
        for event in detail["events"]
    )


def test_audit_contains_evidence_and_rejected_attempts(client):
    token, created, detail = _pending(client, message_id="audit-1")
    sam = _login(client, SAM)
    client.post(
        f"/api/approvals/{detail['pending_approval']['id']}/respond",
        headers=_auth(sam),
        json={"decision": "approve"},
    )
    detail = _detail(client, token, created["task_id"])
    proposed = [event for event in detail["events"] if event["action"] == "plan_proposed"]
    assert proposed
    assert proposed[-1]["evidence_refs"]
    assert proposed[-1]["allowed"] is True
    denied = [event for event in detail["events"] if event["action"] == "approval_denied"]
    assert denied
    assert denied[-1]["allowed"] is False
    assert denied[-1]["evidence_refs"]


def test_tenant_isolation(client):
    created = _inbound(client, DEMO_REQUEST, "tenant-acme")
    maya = _login(client, MAYA)
    riley = _login(client, RILEY)
    acme_list = client.get("/api/tasks", headers=_auth(maya))
    beta_list = client.get("/api/tasks", headers=_auth(riley))
    assert acme_list.status_code == 200
    assert beta_list.status_code == 200
    assert {row["public_code"] for row in acme_list.json()["tasks"]} == {created["public_code"]}
    assert {row["public_code"] for row in beta_list.json()["tasks"]} == {"BETA-1001"}
    beta_id = sid("task-beta")
    hidden = client.get(f"/api/tasks/{beta_id}", headers=_auth(maya))
    assert hidden.status_code == 404
    assert "not found" in hidden.text.lower()
    leaked = client.get(f"/api/tasks/{created['task_id']}", headers=_auth(riley))
    assert leaked.status_code == 404
    other_approval = client.post(
        "/api/approvals/00000000-0000-0000-0000-000000000099/respond",
        headers=_auth(riley),
        json={"decision": "approve"},
    )
    assert other_approval.status_code == 404


def test_demo_routes_disabled_outside_demo_mode(client):
    settings.app_env = "production"
    try:
        blocked = client.post(
            "/dev/inbound-message",
            json={"sender": MAYA, "text": DEMO_REQUEST, "provider_message_id": "prod-1"},
        )
        assert blocked.status_code == 404
    finally:
        settings.app_env = "development"
    settings.demo_mode = False
    try:
        blocked = client.post(
            "/dev/reset",
            json={"include_demo_task": False},
        )
        assert blocked.status_code == 404
    finally:
        settings.demo_mode = True
    health = client.get("/health")
    assert health.status_code == 200


def test_acknowledge_completes_with_delivery_pending_label(client):
    token, created, detail = _pending(client, message_id="ack-1")
    client.post(
        f"/api/approvals/{detail['pending_approval']['id']}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    acked = client.post(f"/dev/tasks/{created['task_id']}/acknowledge-orders", headers=_auth(token))
    assert acked.status_code == 200, acked.text
    detail = _detail(client, token, created["task_id"])
    assert detail["task"]["status"] == "COMPLETED"
    assert detail["task"]["completion_label"] == "orders confirmed; delivery pending"
    assert all(row["status"] == "ACKNOWLEDGED" for row in detail["orders"])
    assert any(event["action"] == "orders_confirmed" for event in detail["events"])
