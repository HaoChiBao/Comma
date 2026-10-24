import json
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.email import verify_email_signature
from app.adapters.photon import SignatureError, verify_and_parse
from app.auth import current_actor, issue_token, require_task
from app.clock import clock
from app.db import get_db
from app.errors import ApiError, IntegrationNotConfigured
from app.models import Actor, Company, Outbox, PurchaseOrder, WorkEvent
from app.pipeline import (
    acknowledge_orders,
    disrupt_ram,
    handle_inbound_text,
    handle_supplier_reply,
    reject_order,
    simulate_all_replies,
)
from app.adapters.photon import InboundMessage
from app.settings import settings
from app.views import sandbox_for_task, task_detail, task_summary

router = APIRouter()


class LoginIn(BaseModel):
    phone_e164: str


class ApprovalIn(BaseModel):
    decision: str
    reason: str | None = None


class InboundIn(BaseModel):
    sender: str
    text: str
    provider_message_id: str | None = None


class SupplierReplyIn(BaseModel):
    body: str
    subject: str = ""
    provider_message_id: str | None = None
    rfq_token: str | None = None


class DisruptIn(BaseModel):
    reduce_by: int = 50


class RejectPoIn(BaseModel):
    po_number: str
    reason: str


class ResetIn(BaseModel):
    include_demo_task: bool = True


def require_demo() -> None:
    if not settings.demo_routes_enabled:
        raise ApiError(404, "not_found", "not found")


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/api/meta")
def meta():
    return {
        "photon_mode": settings.photon_mode,
        "email_mode": settings.email_mode,
        "demo_mode": settings.demo_mode,
        "demo_routes_enabled": settings.demo_routes_enabled,
        "llm_mode": settings.llm_mode,
        "live_imessage": False,
        "live_email": False,
        "app_env": settings.app_env,
        "demo_clock": clock.now().isoformat(),
        "banner": "Mock mode — no live iMessage or email delivery. Supplier mail stays in the sandbox.",
    }


@router.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    actor = db.scalar(select(Actor).where(Actor.phone_e164 == body.phone_e164, Actor.active.is_(True)))
    if actor is None:
        raise ApiError(401, "unauthorized", "unknown phone")
    company = db.get(Company, actor.company_id)
    return {
        "token": issue_token(actor.id),
        "actor": {
            "id": str(actor.id),
            "display_name": actor.display_name,
            "role": actor.role,
            "phone_e164": actor.phone_e164,
            "company_id": str(actor.company_id),
            "company_name": company.name if company else "",
        },
    }


@router.get("/api/me")
def me(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    company = db.get(Company, actor.company_id)
    return {
        "id": str(actor.id),
        "display_name": actor.display_name,
        "role": actor.role,
        "company_id": str(actor.company_id),
        "company_name": company.name if company else "",
    }


@router.post("/webhooks/photon")
async def photon_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
        inbound = verify_and_parse(raw, payload, dict(request.headers))
    except SignatureError as exc:
        raise ApiError(exc.status, "bad_signature", exc.detail) from exc
    except json.JSONDecodeError as exc:
        raise ApiError(400, "bad_request", "invalid json") from exc
    return handle_inbound_text(db, inbound)


@router.post("/webhooks/email")
async def email_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        verify_email_signature(raw, request.headers.get("x-email-signature"))
    except IntegrationNotConfigured as exc:
        raise ApiError(401, "bad_signature", exc.detail) from exc
    except PermissionError as exc:
        raise ApiError(401, "bad_signature", str(exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(400, "bad_request", "invalid json") from exc
    return handle_supplier_reply(
        db,
        provider_message_id=str(payload.get("provider_message_id") or ""),
        body=payload.get("body") or "",
        subject=payload.get("subject") or "",
        raw=payload,
    )


@router.get("/api/tasks")
def list_tasks(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from app.models import ProcurementTask

    rows = db.scalars(
        select(ProcurementTask)
        .where(ProcurementTask.company_id == actor.company_id)
        .order_by(ProcurementTask.created_at.desc())
    ).all()
    return {"tasks": [task_summary(db, row) for row in rows]}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return task_detail(db, task)


@router.get("/api/tasks/{task_id}/events")
def task_events(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return {"events": task_detail(db, task)["events"]}


@router.get("/api/tasks/{task_id}/quotes")
def task_quotes(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    detail = task_detail(db, task)
    return {"quotes": [row for row in detail["quotes"] if row["status"] == "VALID"], "all_quotes": detail["quotes"]}


@router.get("/api/tasks/{task_id}/plans")
def task_plans(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return {"plans": task_detail(db, task)["plans"]}


@router.get("/api/tasks/{task_id}/orders")
def task_orders(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return {"orders": task_detail(db, task)["orders"]}


@router.get("/api/tasks/{task_id}/rfqs")
def task_rfqs(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return {"rfqs": task_detail(db, task)["rfqs"]}


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task_route(task_id: uuid.UUID, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from app.pipeline import cancel_task

    return cancel_task(db, actor, task_id)


@router.post("/api/approvals/{approval_id}/respond")
def respond_approval(
    approval_id: uuid.UUID,
    body: ApprovalIn,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    from app.gateway import respond

    return respond(db, approval_id=approval_id, actor_id=actor.id, decision=body.decision, reason=body.reason)


@router.get("/api/events/stream")
def event_stream(
    max_batches: int = 1,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    company_id = actor.company_id

    def generate():
        seen: set[str] = set()
        batches = max(1, min(max_batches, 30))
        for _ in range(batches):
            session = next(get_db())
            try:
                rows = session.scalars(
                    select(WorkEvent).where(WorkEvent.company_id == company_id).order_by(WorkEvent.created_at)
                ).all()
                for row in rows:
                    key = str(row.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    payload = {"id": key, "action": row.action, "task_id": str(row.task_id) if row.task_id else None, "allowed": row.allowed}
                    yield f"id: {key}\ndata: {json.dumps(payload)}\n\n"
            finally:
                session.close()
            if batches == 1:
                break
            time.sleep(0.2)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/api/dead-letters")
def dead_letters(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Outbox).where(Outbox.company_id == actor.company_id, Outbox.status == "dead")).all()
    return {
        "dead_letters": [
            {"id": str(row.id), "kind": row.kind, "last_error": row.last_error, "aggregate_id": row.aggregate_id}
            for row in rows
        ]
    }


@router.post("/dev/inbound-message")
def dev_inbound(body: InboundIn, _: None = Depends(require_demo), db: Session = Depends(get_db)):
    inbound = InboundMessage(
        provider_message_id=body.provider_message_id or f"dev-{uuid.uuid4().hex}",
        sender=body.sender,
        recipient=settings.photon_sender_id,
        text=body.text,
        content_type="text",
        sent_at=clock.now(),
        raw={"dev": True, "simulated": True},
    )
    return handle_inbound_text(db, inbound)


@router.post("/dev/supplier-reply")
def dev_supplier_reply(body: SupplierReplyIn, _: None = Depends(require_demo), db: Session = Depends(get_db)):
    email_body = body.body
    if body.rfq_token and "RFQ-TOKEN:" not in email_body:
        email_body = f"RFQ-TOKEN: {body.rfq_token}\n{email_body}"
    return handle_supplier_reply(
        db,
        provider_message_id=body.provider_message_id or f"dev-email-{uuid.uuid4().hex}",
        body=email_body,
        subject=body.subject,
        raw={"dev": True},
    )


@router.post("/dev/tasks/{task_id}/simulate-replies")
def dev_simulate(task_id: uuid.UUID, _: None = Depends(require_demo), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return simulate_all_replies(db, task)


@router.post("/dev/tasks/{task_id}/disrupt-ram")
def dev_disrupt(
    task_id: uuid.UUID,
    body: DisruptIn | None = None,
    _: None = Depends(require_demo),
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    task = require_task(db, actor, task_id)
    reduce_by = body.reduce_by if body else 50
    try:
        return disrupt_ram(db, task, reduce_by=reduce_by)
    except ValueError as exc:
        raise ApiError(409, "not_ready", str(exc)) from exc


@router.post("/dev/tasks/{task_id}/acknowledge-orders")
def dev_ack(task_id: uuid.UUID, _: None = Depends(require_demo), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return acknowledge_orders(db, task)


@router.post("/dev/tasks/{task_id}/reject-order")
def dev_reject(
    task_id: uuid.UUID,
    body: RejectPoIn,
    _: None = Depends(require_demo),
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    task = require_task(db, actor, task_id)
    return reject_order(db, task, body.po_number, body.reason)


@router.post("/dev/reset")
def dev_reset(body: ResetIn, _: None = Depends(require_demo)):
    from app.bootstrap import seed

    seed(reset=True, include_demo_task=body.include_demo_task)
    return {"ok": True, "include_demo_task": body.include_demo_task}


@router.get("/dev/outbox")
def dev_outbox(_: None = Depends(require_demo), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Outbox).where(Outbox.company_id == actor.company_id).order_by(Outbox.created_at)).all()
    return {
        "outbox": [
            {
                "id": str(row.id),
                "kind": row.kind,
                "status": row.status,
                "attempts": row.attempts,
                "provider_ref": row.provider_ref,
                "last_error": row.last_error,
                "payload": row.payload_json,
                "simulated": True,
                "delivered_to_network": False,
            }
            for row in rows
        ]
    }


@router.get("/dev/tasks/{task_id}/sandbox")
def dev_sandbox(task_id: uuid.UUID, _: None = Depends(require_demo), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    task = require_task(db, actor, task_id)
    return {"sandbox": sandbox_for_task(db, task.id), "label": "Sandbox only. No live iMessage or email delivery."}


@router.post("/dev/worker/tick")
def dev_tick(_: None = Depends(require_demo)):
    from app.worker import drain

    return {"processed": drain()}
