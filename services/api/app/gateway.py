import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import clock
from app.errors import ApiError
from app.models import Actor, Approval, Outbox, PurchaseOrder, PurchasePlan, PurchasePlanLine, Supplier, ProcurementTask
from app.money import money, money_str
from app.state import transition

CONTROLLER_LIMIT = Decimal("100000000000.00")


def actor_limit(role: str) -> Decimal:
    from app.settings import settings

    if role == "controller":
        return CONTROLLER_LIMIT
    if role == "procurement_manager":
        return money(settings.manager_approval_limit)
    return Decimal("0.00")


def respond(
    session: Session,
    *,
    approval_id: uuid.UUID,
    actor_id: uuid.UUID,
    decision: str,
    reason: str | None = None,
    message_id: uuid.UUID | None = None,
) -> dict:
    """The only path that can create purchase orders. Commits before returning."""
    approved_new = False
    try:
        actor = session.get(Actor, actor_id)
        if actor is None or not actor.active:
            raise ApiError(401, "unauthorized", "unknown actor")
        approval = session.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
        if approval is None:
            raise ApiError(404, "not_found", "not found")
        task = session.get(ProcurementTask, approval.task_id)
        plan = session.get(PurchasePlan, approval.plan_id)
        if task is None or task.company_id != actor.company_id:
            raise ApiError(404, "not_found", "not found")
        if approval.status == "approved":
            orders = _orders_for_plan(session, plan.id)
            session.commit()
            return {"ok": True, "idempotent": True, "purchase_orders": [_po_dict(row) for row in orders], "task_id": str(task.id)}
        if approval.status != "pending":
            _deny(session, task, actor, approval, f"approval is {approval.status}", message_id)
            session.commit()
            raise ApiError(409, "approval_denied", f"approval is {approval.status}")
        if actor.role not in ("procurement_manager", "controller") or actor_limit(actor.role) <= 0:
            _deny(session, task, actor, approval, "actor is not allowed to approve purchases", message_id)
            session.commit()
            raise ApiError(403, "approval_denied", "actor is not allowed to approve purchases")
        if task.status == "CANCELLED":
            _deny(session, task, actor, approval, "task is cancelled", message_id)
            session.commit()
            raise ApiError(409, "approval_denied", "task is cancelled")
        if clock.now() > approval.expires_at:
            approval.status = "expired"
            approval.responded_at = clock.now()
            _deny(session, task, actor, approval, "approval expired", message_id, action="approval_expired")
            session.commit()
            raise ApiError(409, "approval_denied", "approval expired")
        if plan is None or plan.plan_hash != approval.plan_hash or plan.status != "proposed":
            approval.status = "invalidated"
            approval.responded_at = clock.now()
            _deny(session, task, actor, approval, "plan hash or version is stale", message_id, action="approval_stale_plan")
            session.commit()
            raise ApiError(409, "approval_denied", "plan hash or version is stale")
        amount = money(plan.total_landed_cost or 0)
        if amount > actor_limit(actor.role):
            _deny(
                session,
                task,
                actor,
                approval,
                f"plan total {money_str(amount)} exceeds {actor.role} limit {money_str(actor_limit(actor.role))}",
                message_id,
                action="approval_over_limit",
            )
            session.commit()
            raise ApiError(403, "approval_denied", "plan total exceeds actor approval limit")
        if decision == "reject":
            _reject(session, task, actor, approval, plan, reason or "rejected", message_id)
            session.commit()
            from app.jobs import kick

            kick()
            return {"ok": True, "rejected": True, "task_id": str(task.id), "purchase_orders": []}
        if decision != "approve":
            _deny(session, task, actor, approval, "decision must be approve or reject", message_id)
            session.commit()
            raise ApiError(400, "approval_denied", "decision must be approve or reject")
        orders = _issue_pos(session, task, actor, approval, plan, message_id)
        approved_new = True
        session.commit()
    except ApiError:
        raise
    except IntegrityError:
        session.rollback()
        orders = _orders_for_plan(session, approval_id and _plan_id(session, approval_id))
        return {"ok": True, "idempotent": True, "purchase_orders": [_po_dict(row) for row in orders], "task_id": None}
    except Exception:
        session.rollback()
        raise
    if approved_new:
        from app.jobs import kick

        kick()
    return {
        "ok": True,
        "idempotent": False,
        "purchase_orders": [_po_dict(row) for row in orders],
        "task_id": str(task.id),
    }


def _plan_id(session: Session, approval_id: uuid.UUID):
    approval = session.get(Approval, approval_id)
    return approval.plan_id if approval else None


def _orders_for_plan(session: Session, plan_id) -> list[PurchaseOrder]:
    if plan_id is None:
        return []
    return list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.plan_id == plan_id)).all())


def _deny(session, task, actor, approval, reason, message_id, action="approval_denied"):
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=actor.role,
        actor_id=actor.id,
        action=action,
        previous_state=approval.status,
        new_state=approval.status,
        reason=reason,
        evidence_refs=[str(approval.id), str(approval.plan_id), str(message_id) if message_id else None],
        allowed=False,
    )


def _reject(session, task, actor, approval, plan, reason, message_id):
    approval.status = "rejected"
    approval.reason = reason
    approval.responded_at = clock.now()
    approval.response_message_id = message_id
    plan.status = "rejected"
    supplier_count = len({row.supplier_id for row in session.scalars(select(PurchasePlanLine).where(PurchasePlanLine.plan_id == plan.id)).all()})
    preferences = dict(task.preferences_json or {})
    if "fewer supplier" in reason.lower():
        preferences["max_suppliers"] = max(1, supplier_count - 1)
        task.preferences_json = preferences
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=actor.role,
        actor_id=actor.id,
        action="approval_rejected",
        previous_state="pending",
        new_state="rejected",
        reason=reason,
        diff={"plan_id": str(plan.id), "preferences": preferences},
        evidence_refs=[str(approval.id), str(plan.id), str(message_id) if message_id else None],
        allowed=True,
    )
    if task.status != "EVALUATING":
        transition(
            session,
            task,
            "EVALUATING",
            actor_type=actor.role,
            actor_id=actor.id,
            reason=f"approval rejected: {reason}",
            evidence_refs=[str(approval.id)],
            action="reopen_after_rejection",
        )
    from app.jobs import enqueue_advance

    enqueue_advance(session, company_id=task.company_id, task_id=task.id)
    _queue_message(
        session,
        task,
        actor_id=actor.id,
        phone=_phone(session, task),
        text=f"{task.public_code} was rejected ({reason}). I will not place that order. Replanning now.",
        correlation_id=f"reject-{approval.token}",
        idem=f"photon:reject:{approval.id}",
    )


def _issue_pos(session, task, actor, approval, plan, message_id) -> list[PurchaseOrder]:
    session.execute(text("SELECT pg_advisory_xact_lock(814202)"))
    lines = list(session.scalars(select(PurchasePlanLine).where(PurchasePlanLine.plan_id == plan.id)).all())
    grouped: dict = defaultdict(list)
    for line in lines:
        grouped[line.supplier_id].append(line)
    assumptions = plan.assumptions_json or {}
    shipping = assumptions.get("shipping_by_supplier") or {}
    taxes = assumptions.get("taxes_by_supplier") or {}
    existing_count = session.scalar(select(PurchaseOrder.id))
    # Count with a lock held so numbers stay unique inside this transaction.
    current = session.query(PurchaseOrder).count()
    created: list[PurchaseOrder] = []
    summaries = []
    now = clock.now()
    for index, (supplier_id, rows) in enumerate(sorted(grouped.items(), key=lambda item: str(item[0])), start=1):
        supplier = session.get(Supplier, supplier_id)
        goods = sum((money(row.extended_cost) for row in rows), Decimal("0"))
        ship = money(shipping.get(str(supplier_id), "0"))
        tax = money(taxes.get(str(supplier_id), "0"))
        amount = money(goods + ship + tax)
        po = PurchaseOrder(
            id=uuid.uuid4(),
            task_id=task.id,
            plan_id=plan.id,
            supplier_id=supplier_id,
            po_number=f"PO-{current + index:05d}",
            amount=amount,
            currency=plan.currency,
            status="AUTHORIZED",
            idempotency_key=f"po:{plan.id}:{supplier_id}",
            created_at=now,
        )
        session.add(po)
        session.flush()
        created.append(po)
        summaries.append(f"{po.po_number} {supplier.name if supplier else supplier_id} {money_str(amount)}")
        body = _po_email(task, po, supplier, rows, ship, tax)
        session.add(
            Outbox(
                id=uuid.uuid4(),
                company_id=task.company_id,
                kind="email_po",
                aggregate_id=str(po.id),
                idempotency_key=f"po-email:{po.id}",
                payload_json={
                    "to": supplier.contact_email if supplier else "",
                    "subject": f"Purchase order {po.po_number}",
                    "body": body,
                    "task_id": str(task.id),
                    "po_id": str(po.id),
                    "simulated": True,
                },
                status="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    approval.status = "approved"
    approval.responded_at = now
    approval.response_message_id = message_id
    plan.status = "executed"
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type="gateway",
        actor_id=actor.id,
        action="purchase_orders_authorized",
        previous_state="pending",
        new_state="approved",
        reason="approval matched actor, company, plan hash, expiry, and limit",
        diff={"po_numbers": [row.po_number for row in created], "total": money_str(plan.total_landed_cost)},
        evidence_refs=[str(approval.id), str(plan.id), plan.plan_hash, str(message_id) if message_id else None],
        allowed=True,
    )
    if task.status != "ORDERING":
        transition(
            session,
            task,
            "ORDERING",
            actor_type="gateway",
            actor_id=actor.id,
            reason="purchase orders authorized",
            evidence_refs=[row.po_number for row in created],
            action="orders_authorized",
        )
    _queue_message(
        session,
        task,
        actor_id=actor.id,
        phone=_phone(session, task),
        text=(
            f"{task.public_code} approved. Purchase orders authorized (not yet supplier-confirmed): "
            + "; ".join(summaries)
            + ". Delivery is still pending."
        ),
        correlation_id=approval.token,
        idem=f"photon:ordered:{approval.id}",
    )
    _ = existing_count
    return created


def _po_email(task, po, supplier, rows, ship, tax) -> str:
    lines = "\n".join(
        f"SKU: {row.sku}  qty {row.quantity}  unit {money_str(row.unit_price)}  ext {money_str(row.extended_cost)}"
        for row in rows
    )
    return (
        f"Purchase order {po.po_number}\n"
        f"Supplier: {supplier.name if supplier else ''}\n"
        f"Task: {task.public_code}\n"
        f"Ship to: {task.destination}\n"
        f"Need by: {task.delivery_deadline}\n"
        f"{lines}\n"
        f"Shipping once: {money_str(ship)}\n"
        f"Taxes once: {money_str(tax)}\n"
        f"PO total: {money_str(po.amount)} {po.currency}\n"
        "This message was produced by the sandbox email adapter and was not delivered to a real inbox.\n"
        "Reply ACK to confirm or REJECT with a reason. Acknowledgement is not delivery."
    )


def _phone(session, task) -> str:
    actor = session.get(Actor, task.created_by) if task.created_by else None
    return actor.phone_e164 if actor else ""


def _queue_message(session, task, *, actor_id, phone, text, correlation_id, idem):
    now = clock.now()
    existing = session.scalar(select(Outbox).where(Outbox.idempotency_key == idem))
    if existing:
        return
    session.add(
        Outbox(
            id=uuid.uuid4(),
            company_id=task.company_id,
            kind="photon_message",
            aggregate_id=str(task.id),
            idempotency_key=idem,
            payload_json={
                "to": phone,
                "text": text,
                "correlation_id": correlation_id,
                "task_id": str(task.id),
                "simulated": True,
            },
            status="pending",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def _po_dict(row: PurchaseOrder) -> dict:
    return {
        "id": str(row.id),
        "po_number": row.po_number,
        "supplier_id": str(row.supplier_id),
        "plan_id": str(row.plan_id),
        "amount": money_str(row.amount),
        "currency": row.currency,
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "provider_ref": row.provider_ref,
    }


def queue_photon(session, task, *, text: str, correlation_id: str, idem: str) -> None:
    _queue_message(session, task, actor_id=task.created_by, phone=_phone(session, task), text=text, correlation_id=correlation_id, idem=idem)
