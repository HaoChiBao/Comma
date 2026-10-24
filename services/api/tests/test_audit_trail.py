"""Audit trail, cancelled-task approval, and tenant write checks."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Approval, ProcurementTask, PurchaseOrder, WorkEvent
from app.settings import settings
from app.state import InvalidTransition, transition
from app.worker import _apply_business_effect
from test_acceptance import MAYA, RILEY, SAM, _auth, _detail, _inbound, _login, _pending


def _events(client, token, task_id):
    response = client.get(f"/api/tasks/{task_id}/events", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["events"]


def _signature(event: dict) -> tuple:
    return (
        event["action"],
        event["allowed"],
        event["previous_state"],
        event["new_state"],
        event["reason"],
        tuple(event["evidence_refs"]),
        event["actor_type"],
        event["actor_id"],
        event["created_at"],
    )


def test_invalid_transition_does_not_change_state():
    session = SessionLocal()
    task = session.scalar(select(ProcurementTask).where(ProcurementTask.public_code == "BETA-1001"))
    before = task.status
    prior = session.scalar(select(func.count()).select_from(WorkEvent).where(WorkEvent.task_id == task.id))
    with pytest.raises(InvalidTransition):
        transition(
            session,
            task,
            "COMPLETED",
            actor_type="system_agent",
            actor_id=None,
            reason="skip the gateway",
            evidence_refs=["gateway-bypass"],
        )
    session.commit()
    session.refresh(task)
    assert task.status == before
    denied = session.scalars(
        select(WorkEvent).where(WorkEvent.task_id == task.id, WorkEvent.action == "transition_denied")
    ).all()
    assert len(denied) == prior + 1
    event = denied[-1]
    assert event.allowed is False
    assert event.previous_state == before
    assert event.new_state == "COMPLETED"
    assert event.reason == "skip the gateway"
    assert event.evidence_refs_json == ["gateway-bypass"]
    assert event.actor_type == "system_agent"
    assert event.created_at is not None
    session.close()


def test_api_events_match_stored_rows_including_denials(client):
    token, created, detail = _pending(client, message_id="audit-match")
    sam = _login(client, SAM)
    denied = client.post(
        f"/api/approvals/{detail['pending_approval']['id']}/respond",
        headers=_auth(sam),
        json={"decision": "approve"},
    )
    assert denied.status_code == 403, denied.text
    api_events = _events(client, token, created["task_id"])
    session = SessionLocal()
    try:
        rows = session.scalars(
            select(WorkEvent).where(WorkEvent.task_id == uuid.UUID(created["task_id"])).order_by(WorkEvent.created_at)
        ).all()
        stored = [
            {
                "action": row.action,
                "allowed": row.allowed,
                "previous_state": row.previous_state,
                "new_state": row.new_state,
                "reason": row.reason,
                "evidence_refs": list(row.evidence_refs_json or []),
                "actor_type": row.actor_type,
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    finally:
        session.close()
    assert len(api_events) == len(stored)
    assert sorted(_signature(row) for row in api_events) == sorted(_signature(row) for row in stored)
    denial = next(row for row in api_events if row["action"] == "approval_denied" and row["allowed"] is False)
    assert denial["previous_state"] == "pending"
    assert denial["new_state"] == "pending"
    assert denial["reason"]
    assert denial["evidence_refs"]
    assert denial["actor_type"] == "requester"
    assert denial["actor_id"]
    assert denial["created_at"]
    assert all(row["orders"] == [] for row in [_detail(client, token, created["task_id"])])


def test_cancelled_task_cannot_issue_purchase_orders(client):
    token, created, detail = _pending(client, message_id="cancel-before-po")
    approval_id = detail["pending_approval"]["id"]
    sam = _login(client, SAM)
    refused = client.post(f"/api/tasks/{created['task_id']}/cancel", headers=_auth(sam))
    assert refused.status_code == 403, refused.text
    assert _detail(client, token, created["task_id"])["task"]["status"] == "AWAITING_APPROVAL"
    cancelled = client.post(f"/api/tasks/{created['task_id']}/cancel", headers=_auth(token))
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    blocked = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert blocked.status_code == 409, blocked.text
    assert "cancelled" in blocked.json()["detail"]
    again = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert again.status_code == 409, again.text
    detail = _detail(client, token, created["task_id"])
    assert detail["task"]["status"] == "CANCELLED"
    assert detail["orders"] == []
    assert any(
        event["action"] == "task_cancelled"
        and event["previous_state"] == "AWAITING_APPROVAL"
        and event["new_state"] == "CANCELLED"
        and event["allowed"] is True
        and event["actor_id"]
        and event["evidence_refs"]
        and event["reason"]
        for event in detail["events"]
    )
    assert any(
        event["action"] == "approval_denied"
        and event["allowed"] is False
        and "cancelled" in (event["reason"] or "")
        and event["previous_state"] == "pending"
        and event["new_state"] == "pending"
        and event["evidence_refs"]
        for event in detail["events"]
    )
    session = SessionLocal()
    try:
        approval = session.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "pending"
        assert session.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.task_id == uuid.UUID(created["task_id"]))) == 0
    finally:
        session.close()


def test_other_tenant_cannot_approve_or_cancel(client):
    token, created, detail = _pending(client, message_id="tenant-write")
    approval_id = detail["pending_approval"]["id"]
    before = _events(client, token, created["task_id"])
    riley = _login(client, RILEY)
    me = client.get("/api/me", headers=_auth(riley))
    assert me.status_code == 200, me.text
    beta_company = me.json()["company_id"]
    probe = client.post(
        f"/api/approvals/{approval_id}/respond",
        headers=_auth(riley),
        json={"decision": "approve"},
    )
    assert probe.status_code == 404, probe.text
    assert created["public_code"] not in probe.text
    assert "95281" not in probe.text
    hidden_cancel = client.post(f"/api/tasks/{created['task_id']}/cancel", headers=_auth(riley))
    assert hidden_cancel.status_code == 404, hidden_cancel.text
    after = _detail(client, token, created["task_id"])
    assert after["task"]["status"] == "AWAITING_APPROVAL"
    assert after["orders"] == []
    assert after["pending_approval"]["status"] == "pending"
    assert len(after["events"]) == len(before)
    session = SessionLocal()
    try:
        denial = session.scalar(
            select(WorkEvent).where(
                WorkEvent.company_id == uuid.UUID(beta_company),
                WorkEvent.action == "approval_denied",
                WorkEvent.allowed.is_(False),
            )
        )
        assert denial is not None
        assert denial.task_id is None
        assert denial.new_state == "denied"
        assert denial.previous_state is None
        assert denial.reason == "approval is not in this company"
        assert denial.evidence_refs_json == [approval_id]
        assert created["public_code"] not in (denial.reason or "")
        cancel_denial = session.scalar(
            select(WorkEvent).where(
                WorkEvent.company_id == uuid.UUID(beta_company),
                WorkEvent.action == "cancel_denied",
            )
        )
        assert cancel_denial is not None
        assert cancel_denial.task_id is None
        assert cancel_denial.allowed is False
        assert cancel_denial.new_state == "denied"
        leaked = session.scalar(
            select(func.count()).select_from(WorkEvent).where(
                WorkEvent.task_id == uuid.UUID(created["task_id"]),
                WorkEvent.actor_id == uuid.UUID(me.json()["id"]),
            )
        )
        assert leaked == 0
    finally:
        session.close()


def test_demo_mutation_routes_are_closed(client):
    token = _login(client, MAYA)
    created = _inbound(client, "Need some computers for the lab", "demo-gate")
    task_id = created["task_id"]
    calls = [
        ("post", "/dev/inbound-message", {"sender": MAYA, "text": "nope", "provider_message_id": "demo-gate-2"}),
        ("post", "/dev/supplier-reply", {"body": "SKU: X\n", "provider_message_id": "demo-gate-email"}),
        ("post", f"/dev/tasks/{task_id}/simulate-replies", None),
        ("post", f"/dev/tasks/{task_id}/disrupt-ram", {"reduce_by": 50}),
        ("post", f"/dev/tasks/{task_id}/acknowledge-orders", None),
        ("post", f"/dev/tasks/{task_id}/reject-order", {"po_number": "PO-00001", "reason": "no"}),
        ("post", "/dev/reset", {"include_demo_task": False}),
        ("post", "/dev/worker/tick", None),
        ("get", "/dev/outbox", None),
        ("get", f"/dev/tasks/{task_id}/sandbox", None),
    ]

    def assert_closed():
        for method, path, body in calls:
            request = getattr(client, method)
            kwargs = {"headers": _auth(token)}
            if body is not None:
                kwargs["json"] = body
            response = request(path, **kwargs)
            assert response.status_code == 404, (path, response.status_code, response.text)

    settings.app_env = "production"
    try:
        assert_closed()
    finally:
        settings.app_env = "development"
    settings.demo_mode = False
    try:
        assert_closed()
    finally:
        settings.demo_mode = True
    assert client.get("/health").status_code == 200
    assert client.get("/api/tasks", headers=_auth(token)).status_code == 200


def test_sent_event_uses_the_purchase_order_previous_state(client):
    token, created, detail = _pending(client, message_id="po-previous")
    approved = client.post(
        f"/api/approvals/{detail['pending_approval']['id']}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    replay = client.post(
        f"/api/approvals/{detail['pending_approval']['id']}/respond",
        headers=_auth(token),
        json={"decision": "approve"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent"] is True
    session = SessionLocal()
    try:
        task_id = uuid.UUID(created["task_id"])
        orders = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.task_id == task_id)).all())
        assert orders
        assert len({(row.plan_id, row.supplier_id) for row in orders}) == len(orders)
        po = orders[0]
        po.status = "SENDING"
        session.commit()
        task = session.get(ProcurementTask, task_id)
        _apply_business_effect(
            session,
            SimpleNamespace(
                kind="email_po",
                company_id=task.company_id,
                provider_ref="audit-sending-ref",
                payload_json={"po_id": str(po.id), "task_id": str(task.id)},
            ),
        )
        session.commit()
        event = session.scalar(
            select(WorkEvent).where(
                WorkEvent.task_id == task_id,
                WorkEvent.action == "purchase_order_sent",
                WorkEvent.previous_state == "SENDING",
            )
        )
        assert event is not None
        assert event.new_state == "SENT"
        assert event.allowed is True
        assert po.po_number in event.evidence_refs_json
        assert "audit-sending-ref" in event.evidence_refs_json
        replayed = session.scalar(
            select(WorkEvent).where(WorkEvent.task_id == task_id, WorkEvent.action == "approval_replayed")
        )
        assert replayed is not None
        assert replayed.previous_state == "approved"
        assert replayed.new_state == "approved"
        assert replayed.allowed is True
        assert po.po_number in replayed.evidence_refs_json or orders[0].po_number in replayed.evidence_refs_json
        assert session.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.task_id == task_id)) == len(orders)
    finally:
        session.close()
