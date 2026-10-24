import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.email import email_adapter
from app.adapters.photon import messaging_adapter
from app.audit import record
from app.clock import clock
from app.db import SessionLocal
from app.models import Message, Outbox, PurchaseOrder, ProcurementTask, Rfq, WorkerJob
from app.state import transition

log = logging.getLogger("procurement.worker")


def drain(max_loops: int = 40) -> int:
    done = 0
    for _ in range(max_loops):
        progressed = False
        if process_job():
            progressed = True
            done += 1
        if process_outbox():
            progressed = True
            done += 1
        if not progressed:
            break
    return done


def process_job() -> bool:
    session = SessionLocal()
    job_id = None
    try:
        now = clock.now()
        job_id = session.execute(
            text(
                """
                SELECT id FROM worker_jobs
                WHERE attempts < 8 AND (
                    (status = 'pending' AND next_run_at <= :now)
                    OR (status = 'leased' AND lease_until IS NOT NULL AND lease_until < :now)
                )
                ORDER BY next_run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ),
            {"now": now},
        ).scalar()
        if job_id is None:
            session.rollback()
            return False
        job = session.get(WorkerJob, job_id)
        job.status = "leased"
        job.lease_until = now + timedelta(seconds=30)
        job.attempts = job.attempts + 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    try:
        job_session = SessionLocal()
        job = job_session.get(WorkerJob, job_id)
        if job.kind == "advance_task" and job.task_id is not None:
            job_session.close()
            from app.orchestrator import advance

            advance(job.task_id)
            job_session = SessionLocal()
            job = job_session.get(WorkerJob, job_id)
        job.status = "done"
        job.lease_until = None
        job_session.commit()
        job_session.close()
        return True
    except Exception as exc:
        log.exception("job %s failed", job_id)
        failed = SessionLocal()
        job = failed.get(WorkerJob, job_id)
        if job is not None:
            job.status = "dead" if job.attempts >= 8 else "pending"
            job.last_error = str(exc)[:500]
            job.next_run_at = clock.now() + timedelta(seconds=2 ** min(job.attempts, 6))
            job.lease_until = None
            failed.commit()
        failed.close()
        return True


def process_outbox() -> bool:
    session = SessionLocal()
    outbox_id = None
    previous = None
    try:
        now = clock.now()
        outbox_id = session.execute(
            text(
                """
                SELECT id FROM outbox
                WHERE attempts < 5 AND (
                    (status IN ('pending', 'failed', 'ambiguous') AND next_attempt_at <= :now)
                    OR (status = 'sending' AND lease_until IS NOT NULL AND lease_until < :now)
                )
                ORDER BY next_attempt_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ),
            {"now": now},
        ).scalar()
        if outbox_id is None:
            session.rollback()
            return False
        item = session.get(Outbox, outbox_id)
        previous = item.status
        item.status = "sending"
        item.lease_until = now + timedelta(seconds=30)
        item.attempts = item.attempts + 1
        item.updated_at = now
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    _deliver(outbox_id, previous)
    return True


def _deliver(outbox_id, previous: str) -> None:
    session = SessionLocal()
    try:
        item = session.get(Outbox, outbox_id)
        if item is None:
            return
        if item.kind == "photon_message":
            adapter = messaging_adapter()
            payload = item.payload_json
            if previous == "ambiguous":
                result = adapter.reconcile(item.idempotency_key)
            else:
                looked = adapter.reconcile(item.idempotency_key)
                result = looked if looked.status == "sent" else adapter.send_message(
                    to=payload.get("to") or "",
                    text=payload.get("text") or "",
                    correlation_id=payload.get("correlation_id") or "",
                    company_id=item.company_id,
                    idempotency_key=item.idempotency_key,
                )
        elif item.kind in ("email_rfq", "email_po"):
            adapter = email_adapter()
            payload = item.payload_json
            if previous == "ambiguous":
                result = adapter.reconcile(item.idempotency_key)
            else:
                looked = adapter.reconcile(item.idempotency_key)
                result = looked if looked.status == "sent" else adapter.send_email(
                    to=payload.get("to") or "",
                    subject=payload.get("subject") or "",
                    body=payload.get("body") or "",
                    company_id=item.company_id,
                    idempotency_key=item.idempotency_key,
                )
        else:
            item.status = "dead"
            item.last_error = f"unknown outbox kind {item.kind}"
            session.commit()
            return
        now = clock.now()
        if result.status == "sent" and result.provider_ref:
            item.status = "sent"
            item.provider_ref = result.provider_ref
            item.lease_until = None
            item.updated_at = now
            _record_message(session, item, result.provider_ref)
            _apply_business_effect(session, item)
        elif result.status == "ambiguous":
            item.status = "ambiguous"
            item.last_error = "ambiguous"
            item.next_attempt_at = now
            item.lease_until = None
            item.updated_at = now
        elif result.status == "unknown":
            item.status = "failed"
            item.last_error = result.error or "unknown delivery"
            item.next_attempt_at = now + timedelta(seconds=2 ** min(item.attempts, 5))
            item.lease_until = None
        else:
            item.status = "dead" if item.attempts >= 5 else "failed"
            item.last_error = result.error or result.status
            item.next_attempt_at = now + timedelta(seconds=2 ** min(item.attempts, 5))
            item.lease_until = None
            item.updated_at = now
        session.commit()
    except Exception as exc:
        session.rollback()
        log.exception("outbox %s failed", outbox_id)
        repair = SessionLocal()
        item = repair.get(Outbox, outbox_id)
        if item is not None:
            item.status = "dead" if item.attempts >= 5 else "failed"
            item.last_error = str(exc)[:500]
            item.next_attempt_at = clock.now() + timedelta(seconds=2 ** min(item.attempts, 5))
            item.lease_until = None
            item.updated_at = clock.now()
            repair.commit()
        repair.close()
    finally:
        session.close()


def _record_message(session: Session, item: Outbox, provider_ref: str) -> None:
    provider = "photon" if item.kind == "photon_message" else "email"
    exists = session.scalar(
        select(Message.id).where(Message.provider == provider, Message.provider_message_id == provider_ref)
    )
    if exists:
        return
    payload = item.payload_json or {}
    task_id = payload.get("task_id")
    session.add(
        Message(
            id=uuid.uuid4(),
            company_id=item.company_id,
            provider=provider,
            provider_message_id=provider_ref,
            direction="outbound",
            task_id=uuid.UUID(task_id) if task_id else None,
            rfq_id=uuid.UUID(payload["rfq_id"]) if payload.get("rfq_id") else None,
            raw_json={"simulated": True, "delivered_to_network": False, "outbox_id": str(item.id)},
            normalized_json={"text": payload.get("text") or payload.get("body"), "to": payload.get("to"), "subject": payload.get("subject")},
            received_at=clock.now(),
        )
    )


def _apply_business_effect(session: Session, item: Outbox) -> None:
    payload = item.payload_json or {}
    if item.kind == "email_rfq" and payload.get("rfq_id"):
        rfq = session.get(Rfq, uuid.UUID(payload["rfq_id"]))
        if rfq is not None and rfq.status == "draft":
            rfq.status = "sent"
            rfq.sent_at = clock.now()
    if item.kind == "email_po" and payload.get("po_id"):
        po = session.get(PurchaseOrder, uuid.UUID(payload["po_id"]))
        if po is not None and po.status in ("AUTHORIZED", "SENDING"):
            po.status = "SENT"
            po.provider_ref = item.provider_ref
            record(
                session,
                company_id=item.company_id,
                task_id=po.task_id,
                actor_type="system_agent",
                actor_id=None,
                action="purchase_order_sent",
                previous_state="AUTHORIZED",
                new_state="SENT",
                reason="sandbox delivery accepted; this is not a supplier acknowledgement",
                evidence_refs=[po.po_number, item.provider_ref or ""],
                allowed=True,
            )
            _maybe_await_confirmation(session, po.task_id)


def _maybe_await_confirmation(session: Session, task_id) -> None:
    task = session.get(ProcurementTask, task_id)
    if task is None or task.status != "ORDERING":
        return
    orders = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.task_id == task_id)).all())
    if not orders:
        return
    if any(order.status in ("DRAFT", "AUTHORIZED", "SENDING") for order in orders):
        return
    if any(order.status == "SENT" for order in orders):
        transition(
            session,
            task,
            "AWAITING_CONFIRMATION",
            actor_type="system_agent",
            actor_id=None,
            reason="purchase orders were handed to the sandbox; supplier acknowledgement is still required",
            evidence_refs=[order.po_number for order in orders],
            action="awaiting_supplier_confirmation",
        )
