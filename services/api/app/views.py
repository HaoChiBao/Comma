from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Approval,
    Message,
    Outbox,
    PurchaseOrder,
    PurchasePlan,
    PurchasePlanLine,
    Rfq,
    Supplier,
    SupplierQuote,
    WorkEvent,
)
from app.money import money, money_str
from app.queries import latest_bom, pending_approval, plan_lines
from app.settings import settings


def task_summary(session: Session, task) -> dict:
    plan = session.scalar(
        select(PurchasePlan)
        .where(PurchasePlan.task_id == task.id, PurchasePlan.status.in_(("proposed", "approved", "executed")))
        .order_by(PurchasePlan.version.desc())
    )
    return {
        "id": str(task.id),
        "public_code": task.public_code,
        "status": task.status,
        "quantity": task.quantity,
        "budget_amount": money_str(task.budget_amount) if task.budget_amount is not None else None,
        "currency": task.currency,
        "delivery_deadline": task.delivery_deadline.isoformat() if task.delivery_deadline else None,
        "destination": task.destination,
        "total_landed_cost": money_str(plan.total_landed_cost) if plan and plan.total_landed_cost is not None else None,
        "pending_action": pending_action(task.status),
        "completion_label": "orders confirmed; delivery pending" if task.status == "COMPLETED" else None,
        "blocked_reason": task.blocked_reason,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def pending_action(status: str) -> str:
    return {
        "NEEDS_INFO": "Reply with the missing constraints",
        "SOURCING": "Agent is checking suppliers",
        "AWAITING_QUOTES": "Waiting on supplier quotes",
        "EVALUATING": "Calculating a purchasing plan",
        "AWAITING_APPROVAL": "Approval required",
        "ORDERING": "Sending purchase orders",
        "AWAITING_CONFIRMATION": "Waiting on supplier acknowledgement",
        "COMPLETED": "Orders confirmed; delivery pending",
        "BLOCKED": "Needs a decision before constraints can change",
        "CANCELLED": "Cancelled",
        "CREATED": "Starting",
    }.get(status, status)


def task_detail(session: Session, task) -> dict:
    version, bom = latest_bom(session, task.id)
    suppliers = {row.id: row for row in session.scalars(select(Supplier).where(Supplier.company_id == task.company_id)).all()}
    plans = list(session.scalars(select(PurchasePlan).where(PurchasePlan.task_id == task.id).order_by(PurchasePlan.version)).all())
    plan_payloads = [_plan(session, plan, suppliers) for plan in plans]
    for payload, plan in zip(plan_payloads, plans):
        attach_headroom(payload, task.budget_amount)
    current = next((item for item in reversed(plan_payloads) if item["status"] in ("proposed", "approved", "executed")), None)
    readiness = current["readiness_computers"] if current else 0
    bom_payload = []
    allocations = {}
    if current:
        for line in current["lines"]:
            allocations.setdefault(line["component_type"], []).append(line)
    for line in bom:
        bom_payload.append(
            {
                "id": str(line.id),
                "component_type": line.component_type,
                "required_sku": line.required_sku,
                "units_per_computer": line.units_per_computer,
                "allowed_substitutes": line.allowed_substitutes_json,
                "spec": line.spec_json,
                "allocations": allocations.get(line.component_type, []),
            }
        )
    return {
        "task": task_summary(session, task),
        "assumptions": task.assumptions_json or [],
        "bom": {"version": version.version if version else None, "lines": bom_payload, "readiness_computers": readiness},
        "rfqs": _rfqs(session, task, suppliers),
        "quotes": _quotes(session, task, suppliers),
        "plans": plan_payloads,
        "approvals": _approvals(session, task),
        "orders": _orders(session, task, suppliers),
        "events": _events(session, task.id),
        "messages": _messages(session, task.id),
        "pending_approval": _approval_dict(pending_approval(session, task.id)),
    }


def _plan(session, plan: PurchasePlan, suppliers: dict) -> dict:
    lines = []
    for line in plan_lines(session, plan.id):
        supplier = suppliers.get(line.supplier_id)
        lines.append(
            {
                "id": str(line.id),
                "bom_line_id": str(line.bom_line_id) if line.bom_line_id else None,
                "supplier_id": str(line.supplier_id),
                "supplier_name": supplier.name if supplier else "",
                "quote_id": str(line.quote_id) if line.quote_id else None,
                "sku": line.sku,
                "component_type": line.component_type,
                "quantity": line.quantity,
                "unit_price": money_str(line.unit_price),
                "extended_cost": money_str(line.extended_cost),
            }
        )
    assumptions = plan.assumptions_json or {}
    headroom = None
    return {
        "id": str(plan.id),
        "version": plan.version,
        "plan_hash": plan.plan_hash,
        "status": plan.status,
        "total_landed_cost": money_str(plan.total_landed_cost) if plan.total_landed_cost is not None else None,
        "currency": plan.currency,
        "readiness_computers": plan.readiness_computers,
        "assumptions": assumptions.get("assumptions") or [],
        "explanation": assumptions.get("explanation"),
        "shipping_by_supplier": assumptions.get("shipping_by_supplier") or {},
        "taxes_by_supplier": assumptions.get("taxes_by_supplier") or {},
        "latest_arrival": assumptions.get("latest_arrival"),
        "per_component_builds": assumptions.get("per_component_builds") or {},
        "binding_constraints": assumptions.get("binding_constraints") or [],
        "goods_cost": assumptions.get("goods_cost"),
        "alternatives": plan.alternatives_json or [],
        "lines": lines,
        "created_at": plan.created_at.isoformat(),
        "budget_headroom": headroom,
    }


def attach_headroom(payload: dict, budget) -> None:
    if budget is None or payload.get("total_landed_cost") is None:
        return
    payload["budget_headroom"] = money_str(money(budget) - money(payload["total_landed_cost"]))


def _rfqs(session, task, suppliers) -> list[dict]:
    rows = session.scalars(select(Rfq).where(Rfq.task_id == task.id).order_by(Rfq.created_at)).all()
    return [
        {
            "id": str(row.id),
            "supplier_id": str(row.supplier_id),
            "supplier_name": suppliers.get(row.supplier_id).name if suppliers.get(row.supplier_id) else "",
            "correlation_token": row.correlation_token,
            "status": row.status,
            "requested": row.requested_json,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        }
        for row in rows
    ]


def _quotes(session, task, suppliers) -> list[dict]:
    rows = session.scalars(select(SupplierQuote).where(SupplierQuote.task_id == task.id).order_by(SupplierQuote.created_at)).all()
    return [
        {
            "id": str(row.id),
            "supplier_id": str(row.supplier_id),
            "supplier_name": suppliers.get(row.supplier_id).name if suppliers.get(row.supplier_id) else "",
            "rfq_id": str(row.rfq_id) if row.rfq_id else None,
            "bom_line_id": str(row.bom_line_id) if row.bom_line_id else None,
            "sku": row.sku,
            "component_type": row.component_type,
            "unit_price": money_str(row.unit_price) if row.unit_price is not None else None,
            "currency": row.currency,
            "available_qty": row.available_qty,
            "min_order_qty": row.min_order_qty,
            "shipping_amount": money_str(row.shipping_amount) if row.shipping_amount is not None else None,
            "taxes_amount": money_str(row.taxes_amount) if row.taxes_amount is not None else None,
            "estimated_arrival": row.estimated_arrival.isoformat() if row.estimated_arrival else None,
            "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            "status": row.status,
            "version": row.version,
            "evidence_ref": row.evidence_ref,
            "uncertainty": row.uncertainty_json or [],
            "spec": row.spec_json or {},
        }
        for row in rows
    ]


def _orders(session, task, suppliers) -> list[dict]:
    rows = session.scalars(select(PurchaseOrder).where(PurchaseOrder.task_id == task.id).order_by(PurchaseOrder.po_number)).all()
    return [
        {
            "id": str(row.id),
            "po_number": row.po_number,
            "supplier_id": str(row.supplier_id),
            "supplier_name": suppliers.get(row.supplier_id).name if suppliers.get(row.supplier_id) else "",
            "plan_id": str(row.plan_id),
            "amount": money_str(row.amount),
            "currency": row.currency,
            "status": row.status,
            "idempotency_key": row.idempotency_key,
            "provider_ref": row.provider_ref,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _approvals(session, task) -> list[dict]:
    rows = session.scalars(select(Approval).where(Approval.task_id == task.id).order_by(Approval.created_at)).all()
    return [_approval_dict(row) for row in rows]


def _approval_dict(row: Approval | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "plan_id": str(row.plan_id),
        "plan_hash": row.plan_hash,
        "status": row.status,
        "token": row.token,
        "expires_at": row.expires_at.isoformat(),
        "requested_from": str(row.requested_from),
        "reason": row.reason,
        "responded_at": row.responded_at.isoformat() if row.responded_at else None,
    }


def _events(session, task_id) -> list[dict]:
    rows = session.scalars(select(WorkEvent).where(WorkEvent.task_id == task_id).order_by(WorkEvent.created_at)).all()
    return [_event(row) for row in rows]


def _event(row: WorkEvent) -> dict:
    return {
        "id": str(row.id),
        "actor_type": row.actor_type,
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "action": row.action,
        "previous_state": row.previous_state,
        "new_state": row.new_state,
        "reason": row.reason,
        "diff": row.diff_json or {},
        "evidence_refs": row.evidence_refs_json or [],
        "allowed": row.allowed,
        "created_at": row.created_at.isoformat(),
    }


def _messages(session, task_id) -> list[dict]:
    rows = session.scalars(select(Message).where(Message.task_id == task_id).order_by(Message.received_at)).all()
    simulated = settings.photon_mode != "live" or settings.email_mode != "smtp"
    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "direction": row.direction,
            "text": (row.normalized_json or {}).get("text"),
            "subject": (row.normalized_json or {}).get("subject"),
            "to": (row.normalized_json or {}).get("to"),
            "received_at": row.received_at.isoformat(),
            "simulated": simulated or bool((row.raw_json or {}).get("simulated")),
            "delivered_to_network": False if settings.photon_mode != "live" and row.provider == "photon" else bool((row.raw_json or {}).get("delivered_to_network")),
        }
        for row in rows
    ]


def sandbox_for_task(session, task_id) -> list[dict]:
    rows = session.scalars(select(Outbox).where(Outbox.aggregate_id == str(task_id)).order_by(Outbox.created_at)).all()
    extra = session.scalars(select(Outbox).order_by(Outbox.created_at)).all()
    seen = {row.id for row in rows}
    for row in extra:
        payload = row.payload_json or {}
        if payload.get("task_id") == str(task_id) and row.id not in seen:
            rows.append(row)
    return [
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
