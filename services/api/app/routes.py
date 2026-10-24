import hmac
import json
import logging
import time
import uuid

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.email import verify_email_signature
from app.adapters.photon import InboundMessage, SignatureError, verify_and_parse
from app.auth import current_actor, issue_token, require_task
from app.clock import clock
from app.db import get_db
from app.errors import ApiError, IntegrationNotConfigured
from app.models import Actor, Company, Outbox, ProcurementTask, PurchaseOrder, WorkEvent
from app.pipeline import (
    acknowledge_orders,
    cancel_task,
    disrupt_ram,
    handle_inbound_text,
    handle_supplier_reply,
    reject_order,
    simulate_all_replies,
)
from app.settings import settings
from app.tools import TOOL_NAMES, ToolContext, execute
from app.views import sandbox_for_task, task_detail, task_summary

CHAT_TOOLS = ("list_tasks", "submit_message", "get_bom", "get_plan", "get_approval", "cancel_task")

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


def require_tool_token(request: Request) -> None:
    expected = settings.tool_api_token
    if not expected:
        raise ApiError(503, "not_configured", "TOOL_API_TOKEN is not set")
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(token, expected):
        raise ApiError(401, "unauthorized", "invalid tool token")


def normalize_phone(value: str) -> str:
    text = value.strip().replace(" ", "").replace("-", "")
    if text.startswith("00"):
        text = f"+{text[2:]}"
    if text.isdigit():
        text = f"+{text}"
    return text


def tool_actor(request: Request, db: Session) -> Actor:
    phone = normalize_phone(request.headers.get("x-sender-phone", ""))
    if not phone:
        raise ApiError(400, "unknown_sender", "X-Sender-Phone is required")
    actor = db.scalar(select(Actor).where(Actor.phone_e164 == phone, Actor.active.is_(True)))
    if actor is None:
        raise ApiError(403, "unknown_sender", "This phone is not a member of an organization")
    return actor


def tool_company(db: Session, actor: Actor) -> Company:
    company = db.get(Company, actor.company_id)
    if company is None:
        raise ApiError(503, "not_ready", "Organization is missing")
    return company


def acme_company(db: Session) -> Company:
    company = db.scalar(select(Company).where(Company.name == "Acme Computing"))
    if company is None:
        raise ApiError(503, "not_ready", "Acme Computing is not seeded")
    return company


def acme_manager(db: Session, company: Company) -> Actor:
    actor = db.scalar(
        select(Actor).where(
            Actor.company_id == company.id,
            Actor.role == "procurement_manager",
            Actor.active.is_(True),
        )
    )
    if actor is None:
        raise ApiError(503, "not_ready", "Acme has no procurement manager")
    return actor


def acme_task(db: Session, company: Company, task_id: object) -> ProcurementTask:
    try:
        parsed = uuid.UUID(str(task_id))
    except ValueError as exc:
        raise ApiError(400, "tool_failed", "task_id must be a uuid") from exc
    task = db.get(ProcurementTask, parsed)
    if task is None or task.company_id != company.id:
        raise ApiError(404, "not_found", "task not found")
    return task


def current_plan(detail: dict) -> dict | None:
    plans = detail.get("plans") or []
    for plan in reversed(plans):
        if plan.get("status") in ("proposed", "approved", "executed"):
            return plan
    return None


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


@router.get("/tools")
def list_tools(request: Request):
    require_tool_token(request)
    return {"tools": [*CHAT_TOOLS, *TOOL_NAMES]}


@router.get("/tools/identity")
def tool_identity(request: Request, db: Session = Depends(get_db)):
    require_tool_token(request)
    actor = tool_actor(request, db)
    company = tool_company(db, actor)
    return {
        "actor_id": str(actor.id),
        "display_name": actor.display_name,
        "role": actor.role,
        "company_id": str(company.id),
        "company_name": company.name,
    }


@router.post("/tools/{name}")
def invoke_tool(
    name: str,
    request: Request,
    db: Session = Depends(get_db),
    payload: dict = Body(default_factory=dict),
):
    require_tool_token(request)
    actor = tool_actor(request, db)
    company = tool_company(db, actor)
    body = payload or {}
    if name == "list_tasks":
        rows = db.scalars(
            select(ProcurementTask)
            .where(ProcurementTask.company_id == company.id)
            .order_by(ProcurementTask.created_at.desc())
        ).all()
        return {
            "tasks": [
                {
                    "id": str(task.id),
                    "public_code": task.public_code,
                    "status": task.status,
                    "quantity": task.quantity,
                }
                for task in rows
            ]
        }
    if name == "submit_message":
        text = str(body.get("text") or "").strip()
        if not text:
            raise ApiError(400, "tool_failed", "text is required")
        return handle_inbound_text(
            db,
            InboundMessage(
                provider_message_id=f"imessage-tool-{uuid.uuid4()}",
                sender=actor.phone_e164,
                recipient=None,
                text=text,
                content_type="text",
                sent_at=clock.now(),
                raw={"source": "imessage_tool"},
            ),
        )
    if name in ("get_bom", "get_plan", "get_approval", "cancel_task"):
        task = acme_task(db, company, body.get("task_id"))
        if name == "cancel_task":
            return cancel_task(db, actor, task.id)
        detail = task_detail(db, task)
        if name == "get_bom":
            return {"task_id": str(task.id), "public_code": task.public_code, "bom": detail["bom"]}
        if name == "get_plan":
            plan = current_plan(detail)
            return {
                "task_id": str(task.id),
                "public_code": task.public_code,
                "plan": plan,
                "note": None if plan else "No saved plan yet.",
            }
        approval = detail.get("pending_approval")
        if approval:
            approver = db.get(Actor, uuid.UUID(approval["requested_from"]))
            approval = {**approval, "approver_name": approver.display_name if approver else None}
        return {
            "task_id": str(task.id),
            "public_code": task.public_code,
            "approval": approval,
            "note": None if approval else "No open approval.",
        }
    if name not in TOOL_NAMES:
        raise ApiError(404, "unknown_tool", name)
    ctx = ToolContext(company_id=company.id, actor_type="imessage_agent", actor_id=actor.id)
    try:
        result = execute(db, ctx, {}, {"tool": name, "input": body})
    except ApiError:
        raise
    except Exception as exc:
        db.rollback()
        logging.getLogger("procurement.tools").exception("tool %s failed", name)
        raise ApiError(400, "tool_failed", f"{type(exc).__name__}: {exc}") from exc
    db.commit()
    return result
