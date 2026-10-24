"""Concurrency, restart, and ambiguous-delivery stress tests against Postgres."""

import threading
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.adapters.photon import InboundMessage, delivery_hook
from app.clock import clock
from app.db import SessionLocal
from app.errors import ApiError
from app.fixtures.catalog import DEMO_REQUEST
from app.gateway import respond
from app.models import Approval, Outbox, ProcurementTask, PurchaseOrder, Rfq, SandboxDelivery, WorkerJob
from app.pipeline import handle_inbound_text
from app.settings import settings
from app.worker import process_job, process_outbox
from test_acceptance import _auth, _detail, _login, _pending

MAYA = "+15555550100"


def test_burst_duplicate_webhooks_one_task():
    settings.eager_worker = False
    inbound = InboundMessage(
        provider_message_id="burst-1",
        sender=MAYA,
        recipient="+15555551000",
        text=DEMO_REQUEST,
        content_type="text",
        sent_at=clock.now(),
        raw={"simulated": True},
    )
    results: list = []
    errors: list = []

    def run():
        session = SessionLocal()
        try:
            results.append(handle_inbound_text(session, inbound))
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            session.close()

    threads = [threading.Thread(target=run) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sum(1 for row in results if row.get("deduped")) == 11
    assert sum(1 for row in results if not row.get("deduped")) == 1
    session = SessionLocal()
    try:
        count = session.scalar(
            select(func.count()).select_from(ProcurementTask).where(ProcurementTask.public_code.like("ACME-%"))
        )
        assert count == 1
    finally:
        session.close()


def test_concurrent_approval_retries_one_po_per_supplier(client):
    token, created, detail = _pending(client, message_id="concurrent-approve")
    approval_id = uuid.UUID(detail["pending_approval"]["id"])
    plan_id = uuid.UUID(detail["pending_approval"]["plan_id"])
    supplier_count = len({line["supplier_id"] for line in next(plan for plan in detail["plans"] if plan["id"] == str(plan_id))["lines"]})
    from app.bootstrap import sid

    actors = [sid("actor-maya"), sid("actor-jordan")]
    outcomes: list = []
    barrier = threading.Barrier(8)

    def run(actor_id):
        barrier.wait()
        session = SessionLocal()
        try:
            outcomes.append(respond(session, approval_id=approval_id, actor_id=actor_id, decision="approve"))
        except ApiError as exc:
            outcomes.append({"error": exc.error, "detail": exc.detail})
        except Exception as exc:
            outcomes.append({"error": type(exc).__name__, "detail": str(exc)})
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(actors[index % 2],)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(outcomes) == 8
    assert all(isinstance(row, dict) and row.get("ok") is True for row in outcomes), outcomes
    assert sum(1 for row in outcomes if row.get("idempotent") is False) == 1
    session = SessionLocal()
    try:
        orders = session.scalars(select(PurchaseOrder).where(PurchaseOrder.plan_id == plan_id)).all()
        assert len(orders) == supplier_count
        assert len({row.supplier_id for row in orders}) == supplier_count
        assert len({row.idempotency_key for row in orders}) == supplier_count
        approval = session.get(Approval, approval_id)
        assert approval.status == "approved"
    finally:
        session.close()
    detail = _detail(client, token, created["task_id"])
    assert len(detail["orders"]) == supplier_count


def test_worker_restart_resumes_rfqs_and_pending_approvals(client):
    settings.eager_worker = False
    created = client.post(
        "/dev/inbound-message",
        json={"sender": MAYA, "text": DEMO_REQUEST, "provider_message_id": "restart-rfq"},
    )
    assert created.status_code == 200, created.text
    task_id = uuid.UUID(created.json()["task_id"])
    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(Rfq).where(Rfq.task_id == task_id)) == 0
        job = session.scalar(select(WorkerJob).where(WorkerJob.task_id == task_id, WorkerJob.status == "pending"))
        assert job is not None
        job.status = "leased"
        job.lease_until = clock.now() - timedelta(seconds=10)
        session.commit()
    finally:
        session.close()
    assert process_job() is True
    session = SessionLocal()
    try:
        rfq_count = session.scalar(select(func.count()).select_from(Rfq).where(Rfq.task_id == task_id))
        assert rfq_count >= 8
        rows = list(session.scalars(select(Outbox).where(Outbox.kind == "email_rfq", Outbox.status == "pending")).all())
        assert rows
        target = rows[0]
        for row in session.scalars(select(Outbox)).all():
            if row.id != target.id:
                row.next_attempt_at = clock.now() + timedelta(days=1)
        target.status = "sending"
        target.lease_until = clock.now() - timedelta(seconds=10)
        target.provider_ref = None
        target.next_attempt_at = clock.now() - timedelta(seconds=10)
        session.commit()
        outbox_id = target.id
    finally:
        session.close()
    assert process_outbox() is True
    session = SessionLocal()
    try:
        sent = session.get(Outbox, outbox_id)
        assert sent.status == "sent"
        assert sent.provider_ref
        deliveries = session.scalars(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == sent.idempotency_key)).all()
        assert len(deliveries) == 1
        assert deliveries[0].payload_json["delivered_to_network"] is False
    finally:
        session.close()

    settings.eager_worker = True
    token, created, detail = _pending(client, message_id="restart-approval")
    assert detail["orders"] == []
    assert detail["pending_approval"]["status"] == "pending"
    approval_id = uuid.UUID(detail["pending_approval"]["id"])
    task_uuid = uuid.UUID(created["task_id"])
    settings.eager_worker = False
    session = SessionLocal()
    try:
        job = WorkerJob(
            id=uuid.uuid4(),
            company_id=sid_company(),
            task_id=task_uuid,
            kind="advance_task",
            status="leased",
            attempts=1,
            next_run_at=clock.now() - timedelta(seconds=5),
            lease_until=clock.now() - timedelta(seconds=5),
            idempotency_key=f"advance:{task_uuid}:restart",
            payload_json={},
            created_at=clock.now(),
        )
        session.add(job)
        session.commit()
    finally:
        session.close()
    assert process_job() is True
    session = SessionLocal()
    try:
        approval = session.get(Approval, approval_id)
        assert approval.status == "pending"
        po_count = session.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.task_id == task_uuid))
        assert po_count == 0
    finally:
        session.close()
    detail = _detail(client, token, created["task_id"])
    assert detail["pending_approval"]["status"] == "pending"
    assert detail["orders"] == []


def test_ambiguous_outbound_is_reconciled_without_a_second_send():
    settings.eager_worker = False
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        created = client.post(
            "/dev/inbound-message",
            json={"sender": MAYA, "text": DEMO_REQUEST, "provider_message_id": "ambiguous-1"},
        )
    assert created.status_code == 200, created.text
    task_id = uuid.UUID(created.json()["task_id"])
    session = SessionLocal()
    try:
        job = session.scalar(select(WorkerJob).where(WorkerJob.task_id == task_id))
        job.status = "leased"
        job.lease_until = clock.now() - timedelta(seconds=5)
        session.commit()
    finally:
        session.close()
    assert process_job() is True
    session = SessionLocal()
    try:
        rows = list(session.scalars(select(Outbox).where(Outbox.status == "pending")).all())
        target = next(row for row in rows if row.kind == "email_rfq")
        target_id = target.id
        for row in rows:
            if row.id != target.id:
                row.next_attempt_at = clock.now() + timedelta(days=1)
        target.next_attempt_at = clock.now() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    delivery_hook["email"] = "ambiguous"
    assert process_outbox() is True
    session = SessionLocal()
    try:
        item = session.get(Outbox, target_id)
        assert item.status == "ambiguous"
        delivery = session.scalar(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == item.idempotency_key))
        assert delivery is not None
        provider_ref = delivery.provider_ref
        item.next_attempt_at = clock.now()
        session.commit()
    finally:
        session.close()
    delivery_hook["email"] = None
    assert process_outbox() is True
    session = SessionLocal()
    try:
        item = session.get(Outbox, target_id)
        assert item.status == "sent"
        assert item.provider_ref == provider_ref
        count = session.scalar(
            select(func.count()).select_from(SandboxDelivery).where(SandboxDelivery.idempotency_key == item.idempotency_key)
        )
        assert count == 1
        po_count = session.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.task_id == task_id))
        assert po_count == 0
    finally:
        session.close()


def test_failed_outbound_retries_once():
    settings.eager_worker = False
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        created = client.post(
            "/dev/inbound-message",
            json={"sender": MAYA, "text": DEMO_REQUEST, "provider_message_id": "fail-mail-1"},
        )
    task_id = uuid.UUID(created.json()["task_id"])
    session = SessionLocal()
    try:
        job = session.scalar(select(WorkerJob).where(WorkerJob.task_id == task_id))
        job.status = "leased"
        job.lease_until = clock.now() - timedelta(seconds=5)
        session.commit()
    finally:
        session.close()
    assert process_job() is True
    session = SessionLocal()
    try:
        rows = list(session.scalars(select(Outbox).where(Outbox.status == "pending")).all())
        target = next(row for row in rows if row.kind == "email_rfq")
        target_id = target.id
        for row in rows:
            if row.id != target.id:
                row.next_attempt_at = clock.now() + timedelta(days=1)
        target.next_attempt_at = clock.now() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    delivery_hook["email"] = "fail"
    assert process_outbox() is True
    session = SessionLocal()
    try:
        item = session.get(Outbox, target_id)
        assert item.status == "failed"
        assert session.scalar(select(SandboxDelivery).where(SandboxDelivery.idempotency_key == item.idempotency_key)) is None
        item.next_attempt_at = clock.now()
        session.commit()
    finally:
        session.close()
    delivery_hook["email"] = None
    assert process_outbox() is True
    session = SessionLocal()
    try:
        item = session.get(Outbox, target_id)
        assert item.status == "sent"
        assert session.scalar(
            select(func.count()).select_from(SandboxDelivery).where(SandboxDelivery.idempotency_key == item.idempotency_key)
        ) == 1
    finally:
        session.close()


def sid_company():
    from app.bootstrap import sid

    return sid("company-acme")
