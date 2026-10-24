import re
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.photon import InboundMessage, _accept
from app.audit import record
from app.clock import clock
from app.extract import extract_intake, parse_quote_email, render_quote_reply
from app.fixtures.catalog import BOM, DEMO_REQUEST
from app.jobs import enqueue_advance, kick
from app.models import (
    Actor,
    Approval,
    BomLine,
    BomVersion,
    CatalogItem,
    Company,
    Message,
    ProcurementTask,
    PurchaseOrder,
    PurchasePlan,
    PurchasePlanLine,
    Rfq,
    Supplier,
    SupplierQuote,
)
from app.money import money, money_str
from app.state import transition

TOKEN_RE = re.compile(r"\b(APV-[A-F0-9]{8})\b", re.I)


def handle_inbound_text(session: Session, inbound: InboundMessage) -> dict:
    try:
        existing = session.scalar(
            select(Message).where(Message.provider == "photon", Message.provider_message_id == inbound.provider_message_id)
        )
        if existing is not None:
            record(
                session,
                company_id=existing.company_id,
                task_id=existing.task_id,
                actor_type="system_agent",
                actor_id=None,
                action="webhook_duplicate_ignored",
                previous_state=None,
                new_state=None,
                reason="duplicate Photon provider message id",
                evidence_refs=[inbound.provider_message_id],
                allowed=True,
            )
            session.commit()
            return {"deduped": True, "task_id": str(existing.task_id) if existing.task_id else None, "message_id": str(existing.id)}

        actor = session.scalar(select(Actor).where(Actor.phone_e164 == inbound.sender, Actor.active.is_(True)))
        message = Message(
            id=uuid.uuid4(),
            company_id=actor.company_id if actor else None,
            provider="photon",
            provider_message_id=inbound.provider_message_id,
            direction="inbound",
            actor_id=actor.id if actor else None,
            raw_json=inbound.raw,
            normalized_json={
                "sender": inbound.sender,
                "recipient": inbound.recipient,
                "text": inbound.text,
                "content_type": inbound.content_type,
            },
            received_at=inbound.sent_at or clock.now(),
        )
        session.add(message)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(Message).where(Message.provider == "photon", Message.provider_message_id == inbound.provider_message_id)
            )
            if existing is None:
                raise
            record(
                session,
                company_id=existing.company_id,
                task_id=existing.task_id,
                actor_type="system_agent",
                actor_id=None,
                action="webhook_duplicate_ignored",
                previous_state=None,
                new_state=None,
                reason="duplicate Photon provider message id",
                evidence_refs=[inbound.provider_message_id],
                allowed=True,
            )
            session.commit()
            return {"deduped": True, "task_id": str(existing.task_id) if existing.task_id else None, "message_id": str(existing.id)}

        if actor is None:
            _unauthorized(session, message, inbound)
            session.commit()
            return {"deduped": False, "task_id": None, "message_id": str(message.id), "authorized": False}

        if inbound.content_type not in ("", "text"):
            record(
                session,
                company_id=actor.company_id,
                task_id=None,
                actor_type="system_agent",
                actor_id=actor.id,
                action="unsupported_message",
                previous_state=None,
                new_state="denied",
                reason=f"content type {inbound.content_type} is not text",
                evidence_refs=[str(message.id)],
                allowed=False,
            )
            session.commit()
            return {"deduped": False, "task_id": None, "message_id": str(message.id), "authorized": True}

        decision, token = _decision_from_text(inbound.text)
        if token or decision:
            routed = _route_approval(session, actor, message, inbound.text, decision, token)
            if routed is not None:
                session.commit()
                kick()
                return routed

        needs = _needs_info_tasks(session, actor)
        if len(needs) > 1 and not _mentions_code(inbound.text, needs):
            _reply(session, needs[0], actor, "More than one request needs detail. Reply with the task code and the missing numbers.")
            session.commit()
            kick()
            return {"deduped": False, "task_id": None, "message_id": str(message.id), "authorized": True, "needs_disambiguation": True}
        if len(needs) == 1:
            task = needs[0]
            message.task_id = task.id
            _apply_intake(session, task, actor, inbound.text, message, merge=True)
            session.commit()
            kick()
            return {"deduped": False, "task_id": str(task.id), "message_id": str(message.id), "authorized": True, "public_code": task.public_code}

        task = _new_task(session, actor, inbound.text, message)
        session.commit()
        kick()
        return {
            "deduped": False,
            "task_id": str(task.id),
            "message_id": str(message.id),
            "authorized": True,
            "public_code": task.public_code,
            "status": task.status,
        }
    except Exception:
        session.rollback()
        raise


def handle_supplier_reply(session: Session, *, provider_message_id: str, body: str, subject: str = "", raw: dict | None = None) -> dict:
    try:
        existing = session.scalar(
            select(Message).where(Message.provider == "email", Message.provider_message_id == provider_message_id)
        )
        if existing is not None:
            record(
                session,
                company_id=existing.company_id,
                task_id=existing.task_id,
                actor_type="system_agent",
                actor_id=None,
                action="email_duplicate_ignored",
                previous_state=None,
                new_state=None,
                reason="duplicate supplier email id",
                evidence_refs=[provider_message_id, str(existing.id)],
                allowed=True,
            )
            session.commit()
            return {"deduped": True, "message_id": str(existing.id), "task_id": str(existing.task_id) if existing.task_id else None}

        parsed = parse_quote_email(body)
        rfq = None
        if parsed.token:
            rfq = session.scalar(select(Rfq).where(Rfq.correlation_token == parsed.token))
        task = session.get(ProcurementTask, rfq.task_id) if rfq else None
        supplier = session.get(Supplier, rfq.supplier_id) if rfq else None
        message = Message(
            id=uuid.uuid4(),
            company_id=task.company_id if task else (supplier.company_id if supplier else None),
            provider="email",
            provider_message_id=provider_message_id,
            direction="inbound",
            rfq_id=rfq.id if rfq else None,
            task_id=task.id if task else None,
            raw_json={"subject": subject, "body": body, "raw": raw or {}, "subject_ignored_for_correlation": True},
            normalized_json={"token": parsed.token, "source": parsed.source},
            received_at=clock.now(),
        )
        session.add(message)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(Message).where(Message.provider == "email", Message.provider_message_id == provider_message_id)
            )
            if existing is None:
                raise
            record(
                session,
                company_id=existing.company_id,
                task_id=existing.task_id,
                actor_type="system_agent",
                actor_id=None,
                action="email_duplicate_ignored",
                previous_state=None,
                new_state=None,
                reason="duplicate supplier email id",
                evidence_refs=[provider_message_id, str(existing.id)],
                allowed=True,
            )
            session.commit()
            return {"deduped": True, "message_id": str(existing.id), "task_id": str(existing.task_id) if existing.task_id else None}
        if rfq is None or task is None:
            record(
                session,
                company_id=message.company_id,
                task_id=None,
                actor_type="system_agent",
                actor_id=None,
                action="email_uncorrelated",
                previous_state=None,
                new_state="denied",
                reason="body did not contain a known RFQ-TOKEN; subject is not authoritative",
                evidence_refs=[str(message.id), subject],
                allowed=False,
            )
            session.commit()
            return {"deduped": False, "correlated": False, "message_id": str(message.id)}

        requested = {item["sku"]: item for item in (rfq.requested_json or [])}
        created = []
        any_valid = False
        any_uncertain = False
        for block in parsed.blocks:
            quote = _store_quote(session, task, rfq, supplier, message, block, requested)
            created.append(quote)
            if quote.status == "VALID":
                any_valid = True
            if quote.status == "NEEDS_REVIEW":
                any_uncertain = True
        if not parsed.blocks:
            any_uncertain = True
            record(
                session,
                company_id=task.company_id,
                task_id=task.id,
                actor_type="system_agent",
                actor_id=None,
                action="quote_needs_review",
                previous_state=rfq.status,
                new_state="needs_review",
                reason="reply did not contain a SKU block",
                evidence_refs=[str(message.id), rfq.correlation_token],
                allowed=True,
            )
        rfq.status = "needs_review" if any_uncertain else "received"
        if any_valid or any_uncertain:
            _invalidate_open_plans(session, task, "supplier quote changed", [str(message.id)])
            if task.status in ("AWAITING_QUOTES", "BLOCKED", "EVALUATING", "AWAITING_APPROVAL", "SOURCING"):
                enqueue_advance(session, company_id=task.company_id, task_id=task.id)
        session.commit()
        kick()
        return {
            "deduped": False,
            "correlated": True,
            "task_id": str(task.id),
            "message_id": str(message.id),
            "quotes": [{"id": str(row.id), "sku": row.sku, "status": row.status} for row in created],
        }
    except Exception:
        session.rollback()
        raise


def simulate_all_replies(session: Session, task: ProcurementTask) -> dict:
    rfqs = list(session.scalars(select(Rfq).where(Rfq.task_id == task.id, Rfq.status.in_(("draft", "sent", "needs_review")))).all())
    results = []
    for rfq in rfqs:
        supplier = session.get(Supplier, rfq.supplier_id)
        lines = []
        for item in rfq.requested_json or []:
            catalog = session.scalar(
                select(CatalogItem).where(CatalogItem.supplier_id == supplier.id, CatalogItem.sku == item["sku"])
            )
            if catalog is None:
                continue
            arrival = clock.today() + timedelta(days=catalog.indicative_lead_days)
            lines.append(
                {
                    "sku": catalog.sku,
                    "unit_price": money_str(catalog.indicative_price),
                    "available_qty": catalog.available_qty,
                    "min_order_qty": catalog.min_order_qty,
                    "shipping_amount": money_str(_line_shipping(supplier, catalog)),
                    "taxes_amount": "0.00",
                    "estimated_arrival": arrival.isoformat(),
                    "valid_until": (clock.today() + timedelta(days=30)).isoformat(),
                }
            )
        body = render_quote_reply(rfq.correlation_token, lines)
        # Commit the current view, then use a fresh session inside the reply handler.
        session.commit()
        reply_session = session
        result = handle_supplier_reply(
            reply_session,
            provider_message_id=f"sandbox-reply-{rfq.id}",
            body=body,
            subject="Sandbox reply",
        )
        results.append(result)
    return {"replies": results}


def _line_shipping(supplier: Supplier, catalog: CatalogItem):
    from app.fixtures.catalog import SUPPLIERS

    for entry in SUPPLIERS:
        if entry["code"] == supplier.code:
            for line in entry["lines"]:
                if line["sku"] == catalog.sku:
                    return money(line["shipping"])
    return money("0.00")


def disrupt_ram(session: Session, task: ProcurementTask, reduce_by: int = 50) -> dict:
    if _active_orders(session, task.id):
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type="system_agent",
            actor_id=None,
            action="quote_revision_after_order",
            previous_state=task.status,
            new_state=task.status,
            reason="RAM disruption ignored for ordering because purchase orders already exist",
            evidence_refs=[str(task.id)],
            allowed=False,
        )
        session.commit()
        return {"ok": False, "detail": "orders already exist"}
    quote = session.scalar(
        select(SupplierQuote)
        .where(
            SupplierQuote.task_id == task.id,
            SupplierQuote.component_type == "ram",
            SupplierQuote.status == "VALID",
        )
        .order_by(SupplierQuote.unit_price.asc(), SupplierQuote.available_qty.desc())
    )
    if quote is None:
        raise ValueError("no valid RAM quote to disrupt")
    previous_qty = int(quote.available_qty or 0)
    new_qty = max(0, previous_qty - reduce_by)
    quote.status = "EXPIRED"
    revised = SupplierQuote(
        id=uuid.uuid4(),
        company_id=quote.company_id,
        task_id=quote.task_id,
        rfq_id=quote.rfq_id,
        supplier_id=quote.supplier_id,
        bom_line_id=quote.bom_line_id,
        sku=quote.sku,
        component_type=quote.component_type,
        unit_price=quote.unit_price,
        currency=quote.currency,
        available_qty=new_qty,
        min_order_qty=quote.min_order_qty,
        shipping_amount=quote.shipping_amount,
        taxes_amount=quote.taxes_amount,
        estimated_arrival=quote.estimated_arrival,
        valid_until=quote.valid_until,
        evidence_ref=f"sandbox-disruption:{quote.id}",
        status="VALID",
        version=quote.version + 1,
        spec_json=quote.spec_json,
        uncertainty_json=[],
        created_at=clock.now(),
    )
    session.add(revised)
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type="system_agent",
        actor_id=None,
        action="quote_revised",
        previous_state="VALID",
        new_state="VALID",
        reason=f"sandbox reduced {quote.sku} available quantity by {reduce_by} (from {previous_qty} to {new_qty})",
        diff={"sku": quote.sku, "from_qty": previous_qty, "to_qty": new_qty, "component": "ram"},
        evidence_refs=[str(quote.id), str(revised.id)],
        allowed=True,
    )
    _invalidate_open_plans(session, task, "RAM availability changed", [str(revised.id)])
    if task.status in ("AWAITING_APPROVAL", "BLOCKED", "AWAITING_QUOTES"):
        transition(
            session,
            task,
            "EVALUATING",
            actor_type="system_agent",
            actor_id=None,
            reason="supplier revised RAM availability; previous plan cannot be executed",
            evidence_refs=[str(revised.id)],
        )
    enqueue_advance(session, company_id=task.company_id, task_id=task.id)
    session.commit()
    kick()
    return {"ok": True, "quote_id": str(revised.id), "available_qty": new_qty, "previous_qty": previous_qty}


def acknowledge_orders(session: Session, task: ProcurementTask) -> dict:
    orders = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.task_id == task.id)).all())
    for order in orders:
        if order.status in ("SENT", "AUTHORIZED", "SENDING"):
            previous = order.status
            order.status = "ACKNOWLEDGED"
            record(
                session,
                company_id=task.company_id,
                task_id=task.id,
                actor_type="system_agent",
                actor_id=None,
                action="purchase_order_acknowledged",
                previous_state=previous,
                new_state="ACKNOWLEDGED",
                reason="sandbox supplier acknowledgement — goods are not delivered",
                evidence_refs=[order.po_number],
                allowed=True,
            )
    if orders and all(order.status == "ACKNOWLEDGED" for order in orders):
        if task.status != "COMPLETED":
            if task.status == "ORDERING":
                transition(
                    session,
                    task,
                    "AWAITING_CONFIRMATION",
                    actor_type="system_agent",
                    actor_id=None,
                    reason="orders sent before acknowledgement",
                    evidence_refs=[order.po_number for order in orders],
                )
            transition(
                session,
                task,
                "COMPLETED",
                actor_type="system_agent",
                actor_id=None,
                reason="orders confirmed; delivery pending",
                evidence_refs=[order.po_number for order in orders],
                action="orders_confirmed",
            )
    session.commit()
    return {"orders": [order.po_number for order in orders], "status": task.status}


def reject_order(session: Session, task: ProcurementTask, po_number: str, reason: str) -> dict:
    order = session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.task_id == task.id, PurchaseOrder.po_number == po_number)
    )
    if order is None:
        return {"ok": False}
    previous = order.status
    order.status = "REJECTED"
    for quote in session.scalars(
        select(SupplierQuote).where(
            SupplierQuote.task_id == task.id,
            SupplierQuote.supplier_id == order.supplier_id,
            SupplierQuote.status == "VALID",
        )
    ).all():
        quote.status = "REJECTED"
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type="system_agent",
        actor_id=None,
        action="purchase_order_rejected",
        previous_state=previous,
        new_state="REJECTED",
        reason=reason,
        evidence_refs=[order.po_number],
        allowed=True,
    )
    plan = session.get(PurchasePlan, order.plan_id)
    if plan is not None and plan.status == "executed":
        plan.status = "invalidated"
    if task.status != "EVALUATING":
        transition(
            session,
            task,
            "EVALUATING",
            actor_type="system_agent",
            actor_id=None,
            reason=f"supplier rejected {order.po_number}: {reason}",
            evidence_refs=[order.po_number],
            action="reopen_after_supplier_rejection",
        )
    enqueue_advance(session, company_id=task.company_id, task_id=task.id)
    session.commit()
    kick()
    return {"ok": True, "po_number": order.po_number, "status": task.status}


def _active_orders(session, task_id) -> bool:
    row = session.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.task_id == task_id,
            PurchaseOrder.status.in_(("AUTHORIZED", "SENDING", "SENT", "ACKNOWLEDGED")),
        )
    )
    return row is not None


def _store_quote(session, task, rfq, supplier, message, block, requested) -> SupplierQuote:
    item = requested.get(block.sku or "")
    uncertainty = list(block.uncertainty)
    status = "VALID"
    spec = {}
    bom_line_id = None
    component_type = None
    if item is None:
        status = "REJECTED"
        uncertainty.append("spec_mismatch")
    else:
        component_type = item.get("component_type")
        spec = item.get("spec") or {}
        if item.get("bom_line_id"):
            bom_line_id = uuid.UUID(item["bom_line_id"])
        catalog = session.scalar(select(CatalogItem).where(CatalogItem.supplier_id == supplier.id, CatalogItem.sku == block.sku))
        if catalog is not None:
            spec = catalog.spec_json or spec
            component_type = catalog.component_type
    if status != "REJECTED" and uncertainty:
        status = "NEEDS_REVIEW"
    version = 1 + (
        session.scalar(
            select(SupplierQuote.version)
            .where(SupplierQuote.rfq_id == rfq.id, SupplierQuote.sku == (block.sku or ""))
            .order_by(SupplierQuote.version.desc())
        )
        or 0
    )
    if status == "VALID":
        for prior in session.scalars(
            select(SupplierQuote).where(
                SupplierQuote.task_id == task.id,
                SupplierQuote.supplier_id == supplier.id,
                SupplierQuote.sku == block.sku,
                SupplierQuote.status == "VALID",
            )
        ).all():
            prior.status = "EXPIRED"
    quote = SupplierQuote(
        id=uuid.uuid4(),
        company_id=task.company_id,
        task_id=task.id,
        rfq_id=rfq.id,
        supplier_id=supplier.id,
        bom_line_id=bom_line_id,
        sku=block.sku or "UNKNOWN",
        component_type=component_type,
        unit_price=money(block.unit_price) if block.unit_price else None,
        currency=block.currency or task.currency,
        available_qty=block.available_qty,
        min_order_qty=block.min_order_qty,
        shipping_amount=money(block.shipping_amount) if block.shipping_amount else None,
        taxes_amount=money(block.taxes_amount) if block.taxes_amount else None,
        estimated_arrival=date.fromisoformat(block.estimated_arrival) if block.estimated_arrival else None,
        valid_until=date.fromisoformat(block.valid_until) if block.valid_until else None,
        evidence_ref=str(message.id),
        status=status,
        version=version,
        spec_json=spec,
        uncertainty_json=uncertainty,
        created_at=clock.now(),
    )
    session.add(quote)
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type="system_agent",
        actor_id=None,
        action="quote_" + status.lower(),
        previous_state=rfq.status,
        new_state=status,
        reason=", ".join(uncertainty) if uncertainty else "structured quote accepted",
        diff={"sku": quote.sku, "unit_price": money_str(quote.unit_price) if quote.unit_price is not None else None},
        evidence_refs=[str(message.id), rfq.correlation_token, str(quote.id)],
        allowed=True,
    )
    return quote


def _invalidate_open_plans(session, task, reason: str, evidence: list) -> None:
    plans = list(session.scalars(select(PurchasePlan).where(PurchasePlan.task_id == task.id, PurchasePlan.status == "proposed")).all())
    for plan in plans:
        plan.status = "invalidated"
        for approval in session.scalars(select(Approval).where(Approval.plan_id == plan.id, Approval.status == "pending")).all():
            approval.status = "invalidated"
            record(
                session,
                company_id=task.company_id,
                task_id=task.id,
                actor_type="system_agent",
                actor_id=None,
                action="approval_invalidated",
                previous_state="pending",
                new_state="invalidated",
                reason=reason,
                evidence_refs=[str(approval.id), str(plan.id), *evidence],
                allowed=True,
            )
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type="system_agent",
            actor_id=None,
            action="plan_invalidated",
            previous_state="proposed",
            new_state="invalidated",
            reason=reason,
            evidence_refs=[str(plan.id), *evidence],
            allowed=True,
        )


def _unauthorized(session, message: Message, inbound: InboundMessage) -> None:
    record(
        session,
        company_id=None,
        task_id=None,
        actor_type="system_agent",
        actor_id=None,
        action="unauthorized_sender",
        previous_state=None,
        new_state="denied",
        reason="sender phone is not an active actor",
        evidence_refs=[str(message.id), inbound.sender],
        allowed=False,
    )
    text = (
        "This phone is not enrolled for procurement. "
        "No request, quote, or order data is available on this thread. "
        "Ask an administrator to authorize the number."
    )
    _accept(
        "photon",
        f"photon:onboarding:{inbound.provider_message_id}",
        None,
        {"to": inbound.sender, "text": text, "correlation_id": "onboarding"},
    )
    session.add(
        Message(
            id=uuid.uuid4(),
            company_id=None,
            provider="photon",
            provider_message_id=f"onboarding-{inbound.provider_message_id}",
            direction="outbound",
            raw_json={"simulated": True, "delivered_to_network": False},
            normalized_json={"text": text, "to": inbound.sender},
            received_at=clock.now(),
        )
    )


def _decision_from_text(text: str) -> tuple[str | None, str | None]:
    match = TOKEN_RE.search(text or "")
    token = match.group(1).upper() if match else None
    if re.search(r"\breject", text or "", re.I):
        return "reject", token
    if re.search(r"\b(approve|approved|yes)\b", text or "", re.I):
        return "approve", token
    return None, token


def _route_approval(session, actor: Actor, message: Message, text: str, decision: str | None, token: str | None):
    from app.gateway import respond

    approval = None
    if token:
        approval = session.scalar(select(Approval).where(Approval.token == token))
        if approval is not None:
            task = session.get(ProcurementTask, approval.task_id)
            if task is None or task.company_id != actor.company_id:
                record(
                    session,
                    company_id=actor.company_id,
                    task_id=None,
                    actor_type=actor.role,
                    actor_id=actor.id,
                    action="approval_denied",
                    previous_state=None,
                    new_state="denied",
                    reason="approval token is not in this company",
                    evidence_refs=[str(message.id)],
                    allowed=False,
                )
                return {"deduped": False, "task_id": None, "message_id": str(message.id), "authorized": True}
    if approval is None and decision:
        eligible = _eligible_approvals(session, actor)
        if len(eligible) == 1:
            approval = eligible[0]
        elif len(eligible) > 1:
            _reply_actor(
                session,
                actor,
                "Several approvals are open. Reply APPROVE or REJECT with the APV token. An unscoped yes is not authorization.",
            )
            record(
                session,
                company_id=actor.company_id,
                task_id=None,
                actor_type=actor.role,
                actor_id=actor.id,
                action="approval_denied",
                previous_state=None,
                new_state="denied",
                reason="unscoped decision while multiple approvals are open",
                evidence_refs=[str(message.id)],
                allowed=False,
            )
            return {"deduped": False, "task_id": None, "message_id": str(message.id), "authorized": True, "ambiguous_approval": True}
        else:
            return None
    if approval is None or decision is None:
        return None
    message.task_id = approval.task_id
    reason = text.strip()
    try:
        result = respond(
            session,
            approval_id=approval.id,
            actor_id=actor.id,
            decision=decision,
            reason=reason,
            message_id=message.id,
        )
    except Exception as exc:
        from app.errors import ApiError

        if isinstance(exc, ApiError):
            return {
                "deduped": False,
                "task_id": str(approval.task_id),
                "message_id": str(message.id),
                "authorized": True,
                "approval_error": exc.error,
                "detail": exc.detail,
            }
        raise
    return {
        "deduped": False,
        "task_id": result.get("task_id") or str(approval.task_id),
        "message_id": str(message.id),
        "authorized": True,
        "approval": result,
    }


def _eligible_approvals(session, actor: Actor) -> list[Approval]:
    if actor.role not in ("procurement_manager", "controller"):
        return []
    rows = session.scalars(select(Approval).where(Approval.status == "pending")).all()
    eligible = []
    for approval in rows:
        task = session.get(ProcurementTask, approval.task_id)
        if task is None or task.company_id != actor.company_id:
            continue
        if actor.role == "controller" or approval.requested_from == actor.id:
            eligible.append(approval)
    return eligible


def _needs_info_tasks(session, actor: Actor) -> list[ProcurementTask]:
    return list(
        session.scalars(
            select(ProcurementTask).where(
                ProcurementTask.company_id == actor.company_id,
                ProcurementTask.created_by == actor.id,
                ProcurementTask.status == "NEEDS_INFO",
            )
        ).all()
    )


def _mentions_code(text: str, tasks: list[ProcurementTask]) -> bool:
    return any(task.public_code in text for task in tasks)


def _new_task(session, actor: Actor, text: str, message: Message) -> ProcurementTask:
    company = session.get(Company, actor.company_id)
    session.execute(sql_text("SELECT pg_advisory_xact_lock(814203)"))
    prefix = "ACME" if company and company.name.startswith("Acme") else "TASK"
    code = _next_code(session, prefix)
    now = clock.now()
    task = ProcurementTask(
        id=uuid.uuid4(),
        company_id=actor.company_id,
        created_by=actor.id,
        public_code=code,
        item_spec_json={},
        currency=company.currency if company else "USD",
        destination=None,
        status="CREATED",
        version=1,
        preferences_json={},
        assumptions_json=[],
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    message.task_id = task.id
    record(
        session,
        company_id=actor.company_id,
        task_id=task.id,
        actor_type=actor.role,
        actor_id=actor.id,
        action="task_created",
        previous_state=None,
        new_state="CREATED",
        reason="inbound message from an authorized phone",
        evidence_refs=[str(message.id)],
        allowed=True,
    )
    _apply_intake(session, task, actor, text, message, merge=False)
    return task


def _apply_intake(session, task: ProcurementTask, actor: Actor, text: str, message: Message, merge: bool) -> None:
    extracted = extract_intake(text)
    spec = dict(task.item_spec_json or {})
    if extracted.product:
        spec["product"] = extracted.product
    if extracted.quantity:
        spec["quantity"] = extracted.quantity
    if extracted.budget:
        spec["budget"] = extracted.budget
    if extracted.deadline:
        spec["deadline"] = extracted.deadline
    if extracted.destination:
        spec["destination"] = extracted.destination
    spec["last_message_id"] = str(message.id)
    task.item_spec_json = spec
    missing = []
    if spec.get("product") != "desktop":
        missing.append("product")
    if not spec.get("quantity"):
        missing.append("quantity")
    if not spec.get("budget"):
        missing.append("budget")
    if not spec.get("deadline"):
        missing.append("delivery deadline")
    if missing:
        if task.status != "NEEDS_INFO":
            transition(
                session,
                task,
                "NEEDS_INFO",
                actor_type="system_agent",
                actor_id=None,
                reason="missing " + ", ".join(missing),
                evidence_refs=[str(message.id)],
                action="clarification_requested",
            )
        task.blocked_reason = "Need " + ", ".join(missing)
        _reply(
            session,
            task,
            actor,
            f"{task.public_code} needs a few details before I can source a desktop build: {', '.join(missing)}. "
            "Send quantity, total budget, and delivery date. I will not guess them.",
        )
        return
    company = session.get(Company, task.company_id)
    task.quantity = int(spec["quantity"])
    task.budget_amount = money(spec["budget"])
    task.delivery_deadline = date.fromisoformat(spec["deadline"])
    assumptions = list(task.assumptions_json or [])
    if spec.get("destination"):
        task.destination = spec["destination"]
    else:
        task.destination = company.ship_to if company else "company ship-to"
        note = "Destination defaulted to the company ship-to because the message did not name one."
        if note not in assumptions:
            assumptions.append(note)
    task.assumptions_json = assumptions
    task.item_spec_json = {**spec, "template": "ACME-DESKTOP-STANDARD"}
    if session.scalar(select(BomVersion.id).where(BomVersion.task_id == task.id)) is None:
        _create_bom(session, task)
    if task.status != "SOURCING":
        transition(
            session,
            task,
            "SOURCING",
            actor_type="system_agent",
            actor_id=None,
            reason="intake has quantity, budget, deadline, and the standard desktop BOM",
            evidence_refs=[str(message.id)],
            action="intake_completed",
        )
    task.blocked_reason = None
    enqueue_advance(session, company_id=task.company_id, task_id=task.id)


def _create_bom(session, task: ProcurementTask) -> None:
    version = BomVersion(id=uuid.uuid4(), task_id=task.id, version=1, status="active")
    session.add(version)
    session.flush()
    for position, line in enumerate(BOM):
        session.add(
            BomLine(
                id=uuid.uuid4(),
                bom_version_id=version.id,
                component_type=line["component_type"],
                required_sku=line["required_sku"],
                units_per_computer=line["units_per_computer"],
                allowed_substitutes_json=line["allowed"],
                spec_json=line["spec"],
                position=position,
            )
        )


def _reply(session, task: ProcurementTask, actor: Actor, text: str) -> None:
    from app.gateway import queue_photon

    queue_photon(session, task, text=text, correlation_id=task.public_code, idem=f"photon:clarify:{task.id}:{message_stamp(text)}")


def _reply_actor(session, actor: Actor, text: str) -> None:
    task = session.scalar(
        select(ProcurementTask).where(ProcurementTask.company_id == actor.company_id).order_by(ProcurementTask.created_at.desc())
    )
    if task is None:
        return
    from app.gateway import queue_photon

    queue_photon(session, task, text=text, correlation_id="ambiguous-approval", idem=f"photon:ambiguous:{actor.id}:{message_stamp(text)}")


def message_stamp(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:10]


def _next_code(session, prefix: str) -> str:
    rows = session.scalars(select(ProcurementTask.public_code)).all()
    numbers = []
    for code in rows:
        if code.startswith(prefix + "-"):
            try:
                numbers.append(int(code.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{max(numbers, default=1000) + 1}"


def cancel_task(session: Session, actor: Actor, task_id: uuid.UUID) -> dict:
    """Audited cancellation. A cancelled task cannot pass the approval gateway."""
    from app.errors import ApiError
    from app.state import InvalidTransition

    task = session.get(ProcurementTask, task_id)
    if task is None or task.company_id != actor.company_id:
        record(
            session,
            company_id=actor.company_id,
            task_id=None,
            actor_type=actor.role,
            actor_id=actor.id,
            action="cancel_denied",
            previous_state=None,
            new_state="denied",
            reason="task is not in this company",
            evidence_refs=[str(task_id)],
            allowed=False,
        )
        session.commit()
        raise ApiError(404, "not_found", "not found")
    allowed_actor = actor.role in ("procurement_manager", "controller") or (
        actor.role == "requester" and task.created_by == actor.id
    )
    if not allowed_actor:
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type=actor.role,
            actor_id=actor.id,
            action="cancel_denied",
            previous_state=task.status,
            new_state=task.status,
            reason="actor is not allowed to cancel this task",
            evidence_refs=[str(task.id), str(actor.id)],
            allowed=False,
        )
        session.commit()
        raise ApiError(403, "cancel_denied", "actor is not allowed to cancel this task")
    try:
        transition(
            session,
            task,
            "CANCELLED",
            actor_type=actor.role,
            actor_id=actor.id,
            reason="requester or approver cancelled the task before purchase orders",
            evidence_refs=[str(actor.id)],
            action="task_cancelled",
        )
    except InvalidTransition as exc:
        session.commit()
        raise ApiError(409, "invalid_transition", str(exc)) from exc
    session.commit()
    return {"ok": True, "task_id": str(task.id), "status": task.status}


def demo_seed_message() -> InboundMessage:
    return InboundMessage(
        provider_message_id="seed-demo-request",
        sender="+15555550100",
        recipient="+15555551000",
        text=DEMO_REQUEST,
        content_type="text",
        sent_at=datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc),
        raw={"seed": True},
    )
