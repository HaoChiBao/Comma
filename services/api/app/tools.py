import json
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import clock
from app.models import (
    Actor,
    Approval,
    CatalogItem,
    Outbox,
    PurchasePlan,
    PurchasePlanLine,
    Rfq,
    Supplier,
    SupplierQuote,
)
from app.money import money, money_str
from app.optimizer import solve
from app.queries import (
    fresh_quotes_for_task,
    latest_bom,
    latest_plan,
    load_rules,
    matching_catalog,
    open_rfq,
    opt_lines,
    opt_quotes,
    quote_is_stale_history,
    supplier_map,
)
from app.settings import settings

TOOL_NAMES = (
    "get_task",
    "list_known_suppliers",
    "get_supplier_catalog",
    "get_valid_quotes",
    "send_rfq",
    "list_pending_rfqs",
    "request_supplier_clarification",
    "calculate_purchase_plan",
    "propose_plan",
    "request_approval",
    "get_task_events",
)


class ToolContext:
    def __init__(self, company_id, actor_type: str = "system_agent", actor_id=None):
        self.company_id = company_id
        self.actor_type = actor_type
        self.actor_id = actor_id
        self.scratch: dict = {}


def execute(session: Session, ctx: ToolContext, obs: dict, action: dict) -> dict:
    name = action["tool"]
    if name not in TOOL_NAMES:
        record(
            session,
            company_id=ctx.company_id,
            task_id=None,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="tool_denied",
            previous_state=None,
            new_state="denied",
            reason=f"tool {name} is not permitted",
            evidence_refs=[name],
            allowed=False,
        )
        raise PermissionError(name)
    data = TOOLS[name](session, ctx, action.get("input") or {})
    return {**obs, **data, "last_tool": name}


def _task(session, ctx, task_id):
    from app.models import ProcurementTask

    task = session.get(ProcurementTask, uuid.UUID(str(task_id)))
    if task is None or task.company_id != ctx.company_id:
        record(
            session,
            company_id=ctx.company_id,
            task_id=None,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="tool_denied",
            previous_state=None,
            new_state="denied",
            reason="task is outside the actor company",
            evidence_refs=[str(task_id)],
            allowed=False,
        )
        raise PermissionError("cross-tenant task")
    return task


def _task_dict(task) -> dict:
    return {
        "id": str(task.id),
        "status": task.status,
        "quantity": task.quantity,
        "budget_amount": money_str(task.budget_amount) if task.budget_amount is not None else None,
        "currency": task.currency,
        "delivery_deadline": task.delivery_deadline.isoformat() if task.delivery_deadline else None,
        "destination": task.destination,
        "public_code": task.public_code,
        "preferences": task.preferences_json or {},
    }


def get_task(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    return {"task": _task_dict(task)}


def list_known_suppliers(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    _, bom = latest_bom(session, task.id)
    suppliers = []
    for supplier in supplier_map(session, task.company_id).values():
        if not supplier.approved or not supplier.active:
            continue
        items = matching_catalog(session, supplier.id, bom)
        if not items:
            continue
        suppliers.append(
            {
                "id": str(supplier.id),
                "name": supplier.name,
                "code": supplier.code,
                "email": supplier.contact_email,
                "skus": [item.sku for item in items],
            }
        )
    return {"suppliers": suppliers}


def get_supplier_catalog(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    _, bom = latest_bom(session, task.id)
    catalog = []
    for supplier in supplier_map(session, task.company_id).values():
        for item in matching_catalog(session, supplier.id, bom):
            catalog.append(
                {
                    "supplier_id": str(supplier.id),
                    "supplier_name": supplier.name,
                    "sku": item.sku,
                    "component_type": item.component_type,
                    "indicative_price": money_str(item.indicative_price),
                    "available_qty": item.available_qty,
                    "lead_days": item.indicative_lead_days,
                    "spec": item.spec_json,
                }
            )
    return {"catalog": catalog}


def get_valid_quotes(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    _, bom = latest_bom(session, task.id)
    quotes = fresh_quotes_for_task(session, task)
    fresh_keys = {(row.supplier_id, row.sku) for row in quotes}
    gaps = []
    for supplier in supplier_map(session, task.company_id).values():
        if not supplier.approved or not supplier.active:
            continue
        needed = []
        stale = False
        for item in matching_catalog(session, supplier.id, bom):
            if (supplier.id, item.sku) in fresh_keys:
                continue
            if quote_is_stale_history(session, supplier.id, item.sku):
                stale = True
            needed.append(item)
        if needed and open_rfq(session, task.id, supplier.id) is None:
            gaps.append(
                {
                    "supplier_id": str(supplier.id),
                    "supplier_name": supplier.name,
                    "reason": "stale" if stale else "missing",
                    "skus": [item.sku for item in needed],
                }
            )
    return {
        "valid_quotes": [
            {
                "id": str(row.id),
                "supplier_id": str(row.supplier_id),
                "sku": row.sku,
                "unit_price": money_str(row.unit_price),
                "available_qty": row.available_qty,
            }
            for row in quotes
        ],
        "rfq_gaps": gaps,
    }


def send_rfq(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    supplier = session.get(Supplier, uuid.UUID(payload["supplier_id"]))
    if supplier is None or supplier.company_id != task.company_id or not supplier.approved:
        raise PermissionError("supplier is not an approved tenant supplier")
    _, bom = latest_bom(session, task.id)
    existing = open_rfq(session, task.id, supplier.id)
    if existing is not None:
        refreshed = get_valid_quotes(session, ctx, {"task_id": str(task.id)})
        return {
            "rfq_gaps": refreshed["rfq_gaps"],
            "last_rfq": {"id": str(existing.id), "token": existing.correlation_token, "duplicate": True},
        }
    items = matching_catalog(session, supplier.id, bom)
    fresh = {(row.supplier_id, row.sku) for row in fresh_quotes_for_task(session, task)}
    requested = []
    stale = False
    for item in items:
        if (supplier.id, item.sku) in fresh:
            continue
        if quote_is_stale_history(session, supplier.id, item.sku):
            stale = True
        line = next(
            (
                bom_line
                for bom_line in bom
                if item.sku == bom_line.required_sku or item.sku in (bom_line.allowed_substitutes_json or [])
            ),
            None,
        )
        units = line.units_per_computer if line else 1
        requested.append(
            {
                "sku": item.sku,
                "component_type": item.component_type,
                "spec": item.spec_json,
                "quantity": int(task.quantity) * units,
                "bom_line_id": str(line.id) if line else None,
                "catalog_id": str(item.id),
            }
        )
    if not requested:
        refreshed = get_valid_quotes(session, ctx, {"task_id": str(task.id)})
        return {"rfq_gaps": refreshed["rfq_gaps"]}
    token = "RFQ" + secrets.token_hex(8)
    rfq = Rfq(
        id=uuid.uuid4(),
        task_id=task.id,
        supplier_id=supplier.id,
        correlation_token=token,
        status="draft",
        response_due_at=clock.now() + timedelta(days=2),
        requested_json=requested,
        created_at=clock.now(),
    )
    session.add(rfq)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        existing = open_rfq(session, task.id, supplier.id)
        refreshed = get_valid_quotes(session, ctx, {"task_id": str(task.id)})
        if existing is None:
            raise
        return {
            "rfq_gaps": refreshed["rfq_gaps"],
            "last_rfq": {"id": str(existing.id), "token": existing.correlation_token, "duplicate": True},
        }
    reason = "stale quote" if stale else "missing quote"
    body = _rfq_body(task, supplier, token, requested)
    now = clock.now()
    session.add(
        Outbox(
            id=uuid.uuid4(),
            company_id=task.company_id,
            kind="email_rfq",
            aggregate_id=str(rfq.id),
            idempotency_key=f"rfq-email:{rfq.id}",
            payload_json={
                "to": supplier.contact_email,
                "subject": f"RFQ {token} — do not correlate on subject alone",
                "body": body,
                "task_id": str(task.id),
                "rfq_id": str(rfq.id),
                "simulated": True,
            },
            status="pending",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        action="rfq_created",
        previous_state=None,
        new_state="draft",
        reason=f"{reason} for {', '.join(item['sku'] for item in requested)}",
        diff={"supplier": supplier.name, "token": token, "skus": [item["sku"] for item in requested]},
        evidence_refs=[str(rfq.id), token],
        allowed=True,
    )
    refreshed = get_valid_quotes(session, ctx, {"task_id": str(task.id)})
    return {"rfq_gaps": refreshed["rfq_gaps"], "last_rfq": {"id": str(rfq.id), "token": token, "reason": reason}}


def list_pending_rfqs(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    rows = session.scalars(select(Rfq).where(Rfq.task_id == task.id, Rfq.status.in_(("draft", "sent")))).all()
    return {
        "pending_rfqs": [
            {"id": str(row.id), "supplier_id": str(row.supplier_id), "token": row.correlation_token, "status": row.status}
            for row in rows
        ]
    }


def request_supplier_clarification(session, ctx, payload):
    rfq = session.get(Rfq, uuid.UUID(payload["rfq_id"]))
    if rfq is None:
        raise PermissionError("unknown rfq")
    task = _task(session, ctx, rfq.task_id)
    supplier = session.get(Supplier, rfq.supplier_id)
    question = payload.get("question") or "Please confirm unit price, shipping, taxes, stock, and arrival."
    token = rfq.correlation_token
    now = clock.now()
    session.add(
        Outbox(
            id=uuid.uuid4(),
            company_id=task.company_id,
            kind="email_rfq",
            aggregate_id=str(rfq.id),
            idempotency_key=f"rfq-clarify:{rfq.id}:{secrets.token_hex(4)}",
            payload_json={
                "to": supplier.contact_email,
                "subject": f"Clarification for RFQ {token}",
                "body": f"RFQ-TOKEN: {token}\n{question}\nSandbox email — not delivered to a real inbox.",
                "task_id": str(task.id),
                "rfq_id": str(rfq.id),
                "simulated": True,
            },
            status="pending",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    rfq.status = "needs_review"
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        action="supplier_clarification_requested",
        previous_state=None,
        new_state="needs_review",
        reason=question,
        evidence_refs=[str(rfq.id), token],
        allowed=True,
    )
    return {"clarification": {"rfq_id": str(rfq.id)}}


def calculate_purchase_plan(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    result = _compute(session, task)
    ctx.scratch["plan"] = result
    return {
        "plan": {
            "feasible": result.feasible,
            "total_landed_cost": money_str(result.total_landed_cost) if result.total_landed_cost is not None else None,
            "readiness_computers": result.readiness_computers,
            "binding_constraints": result.binding_constraints,
            "blocking_components": result.blocking_components,
            "explanation": result.explanation,
            "hash": result.plan_hash,
        }
    }


def propose_plan(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    result = ctx.scratch.get("plan") or _compute(session, task)
    if not result.feasible or result.plan_hash is None:
        return {"proposed": None, "plan": {"feasible": False, "explanation": result.explanation, "binding_constraints": result.binding_constraints}}
    existing = session.scalar(
        select(PurchasePlan).where(
            PurchasePlan.task_id == task.id,
            PurchasePlan.plan_hash == result.plan_hash,
            PurchasePlan.status == "proposed",
        )
    )
    if existing is not None:
        return {"proposed": {"id": str(existing.id), "version": existing.version, "hash": existing.plan_hash}}
    for prior in session.scalars(select(PurchasePlan).where(PurchasePlan.task_id == task.id, PurchasePlan.status == "proposed")).all():
        prior.status = "invalidated"
        record(
            session,
            company_id=task.company_id,
            task_id=task.id,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="plan_invalidated",
            previous_state="proposed",
            new_state="invalidated",
            reason="a new plan version replaced this plan",
            evidence_refs=[str(prior.id), result.plan_hash or ""],
            allowed=True,
        )
        approval = session.scalar(select(Approval).where(Approval.plan_id == prior.id, Approval.status == "pending"))
        if approval is not None:
            approval.status = "invalidated"
            record(
                session,
                company_id=task.company_id,
                task_id=task.id,
                actor_type=ctx.actor_type,
                actor_id=ctx.actor_id,
                action="approval_invalidated",
                previous_state="pending",
                new_state="invalidated",
                reason="a new plan version replaced this approval",
                evidence_refs=[str(approval.id), str(prior.id)],
                allowed=True,
            )
    version = (session.scalar(select(PurchasePlan.version).where(PurchasePlan.task_id == task.id).order_by(PurchasePlan.version.desc())) or 0) + 1
    plan = PurchasePlan(
        id=uuid.uuid4(),
        task_id=task.id,
        version=version,
        plan_hash=result.plan_hash,
        total_landed_cost=result.total_landed_cost,
        currency=task.currency,
        assumptions_json={
            "assumptions": result.assumptions,
            "explanation": result.explanation,
            "shipping_by_supplier": {key: money_str(value) for key, value in result.shipping_by_supplier.items()},
            "taxes_by_supplier": {key: money_str(value) for key, value in result.taxes_by_supplier.items()},
            "latest_arrival": result.latest_arrival.isoformat() if result.latest_arrival else None,
            "per_component_builds": result.per_component_builds,
            "binding_constraints": result.binding_constraints,
            "goods_cost": money_str(result.goods_cost) if result.goods_cost is not None else None,
        },
        alternatives_json=result.rejected_alternatives,
        status="proposed",
        readiness_computers=result.readiness_computers,
        created_at=clock.now(),
    )
    session.add(plan)
    session.flush()
    for line in result.lines:
        session.add(
            PurchasePlanLine(
                id=uuid.uuid4(),
                plan_id=plan.id,
                bom_line_id=uuid.UUID(line.bom_line_id),
                supplier_id=uuid.UUID(line.supplier_id),
                quote_id=uuid.UUID(line.quote_id),
                sku=line.sku,
                component_type=line.component_type,
                quantity=line.quantity,
                unit_price=line.unit_price,
                extended_cost=line.extended_cost,
            )
        )
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        action="plan_proposed",
        previous_state=None,
        new_state="proposed",
        reason=result.explanation,
        diff={"version": version, "total": money_str(result.total_landed_cost), "hash": result.plan_hash},
        evidence_refs=[str(plan.id), result.plan_hash],
        allowed=True,
    )
    return {"proposed": {"id": str(plan.id), "version": version, "hash": result.plan_hash}}


def request_approval(session, ctx, payload):
    task = _task(session, ctx, payload["task_id"])
    plan = session.get(PurchasePlan, uuid.UUID(payload["plan_id"]))
    if plan is None or plan.task_id != task.id or plan.status != "proposed":
        raise PermissionError("plan is not a proposable plan for this task")
    existing = session.scalar(select(Approval).where(Approval.plan_id == plan.id, Approval.status == "pending"))
    if existing is not None:
        return {"approval": {"id": str(existing.id), "token": existing.token, "duplicate": True}}
    approver = _choose_approver(session, task, money(plan.total_landed_cost or 0))
    token = "APV-" + secrets.token_hex(4).upper()
    approval = Approval(
        id=uuid.uuid4(),
        task_id=task.id,
        plan_id=plan.id,
        plan_hash=plan.plan_hash,
        requested_from=approver.id,
        status="pending",
        expires_at=clock.now() + timedelta(hours=settings.approval_ttl_hours),
        token=token,
        created_at=clock.now(),
    )
    session.add(approval)
    assumptions = plan.assumptions_json or {}
    text = (
        f"{task.public_code} plan v{plan.version} is ready for approval.\n"
        f"Total landed cost {money_str(plan.total_landed_cost)} {plan.currency}. "
        f"Hash {plan.plan_hash[:12]}. Readiness {plan.readiness_computers} computers.\n"
        f"{assumptions.get('explanation', '')}\n"
        f"Reply APPROVE {token} or REJECT {token} and a reason. "
        f"An unscoped yes is ignored when more than one approval is open. "
        f"This does not place an order until the approval gateway accepts it."
    )
    from app.gateway import queue_photon

    queue_photon(session, task, text=text, correlation_id=token, idem=f"photon:approval:{approval.id}")
    record(
        session,
        company_id=task.company_id,
        task_id=task.id,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        action="approval_requested",
        previous_state=None,
        new_state="pending",
        reason=f"requested from {approver.display_name}",
        diff={"token": token, "plan_version": plan.version, "total": money_str(plan.total_landed_cost)},
        evidence_refs=[str(approval.id), str(plan.id), plan.plan_hash],
        allowed=True,
    )
    return {"approval": {"id": str(approval.id), "token": token}}


def get_task_events(session, ctx, payload):
    from app.models import WorkEvent

    task = _task(session, ctx, payload["task_id"])
    rows = session.scalars(select(WorkEvent).where(WorkEvent.task_id == task.id).order_by(WorkEvent.created_at)).all()
    return {
        "events": [
            {"id": str(row.id), "action": row.action, "allowed": row.allowed, "reason": row.reason}
            for row in rows
        ]
    }


def _compute(session, task):
    _, bom = latest_bom(session, task.id)
    suppliers = supplier_map(session, task.company_id)
    quotes = opt_quotes(fresh_quotes_for_task(session, task), suppliers)
    preferences = task.preferences_json or {}
    max_suppliers = preferences.get("max_suppliers")
    return solve(
        computer_quantity=int(task.quantity),
        lines=opt_lines(bom),
        quotes=quotes,
        budget=money(task.budget_amount),
        deadline=task.delivery_deadline,
        rules=load_rules(session, task.company_id),
        currency=task.currency,
        max_suppliers=int(max_suppliers) if max_suppliers else None,
    )


def _choose_approver(session, task, total: Decimal) -> Actor:
    actors = session.scalars(select(Actor).where(Actor.company_id == task.company_id, Actor.active.is_(True))).all()
    managers = [actor for actor in actors if actor.role == "procurement_manager"]
    controllers = [actor for actor in actors if actor.role == "controller"]
    creator = session.get(Actor, task.created_by) if task.created_by else None
    if total > money(settings.manager_approval_limit) and controllers:
        return controllers[0]
    if creator is not None and creator.role == "procurement_manager":
        return creator
    if managers:
        return managers[0]
    if controllers:
        return controllers[0]
    raise PermissionError("no approver configured")


def _rfq_body(task, supplier, token, requested) -> str:
    lines = []
    for item in requested:
        lines.append(
            "\n".join(
                [
                    f"SKU: {item['sku']}",
                    f"Component: {item['component_type']}",
                    f"Spec: {json.dumps(item['spec'], sort_keys=True)}",
                    f"Required quantity: {item['quantity']}",
                ]
            )
        )
    return (
        f"RFQ-TOKEN: {token}\n"
        f"RFQ ID: {token}\n"
        f"To: {supplier.name} <{supplier.contact_email}>\n"
        f"Please quote the SKUs below for delivery to {task.destination} by {task.delivery_deadline.isoformat()}.\n"
        "Include unit price, currency, available stock, minimum order quantity, shipping as one order fee "
        "(not per unit), taxes, arrival date YYYY-MM-DD, and the date the quote is valid until.\n"
        "Reply with the RFQ-TOKEN in the body. The subject line is not used for correlation.\n"
        "This message is sandbox email and was not sent to a real mailbox.\n\n"
        + "\n\n".join(lines)
    )


TOOLS = {
    "get_task": get_task,
    "list_known_suppliers": list_known_suppliers,
    "get_supplier_catalog": get_supplier_catalog,
    "get_valid_quotes": get_valid_quotes,
    "send_rfq": send_rfq,
    "list_pending_rfqs": list_pending_rfqs,
    "request_supplier_clarification": request_supplier_clarification,
    "calculate_purchase_plan": calculate_purchase_plan,
    "propose_plan": propose_plan,
    "request_approval": request_approval,
    "get_task_events": get_task_events,
}
