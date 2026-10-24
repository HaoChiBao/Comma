import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import clock
from app.fixtures.catalog import RULES
from app.models import (
    Approval,
    BomLine,
    BomVersion,
    CatalogItem,
    CompatibilityRule,
    PurchasePlan,
    PurchasePlanLine,
    Rfq,
    Supplier,
    SupplierQuote,
)
from app.money import money
from app.optimizer import OptLine, OptQuote
from app.settings import settings


def latest_bom(session: Session, task_id):
    version = session.scalar(
        select(BomVersion).where(BomVersion.task_id == task_id).order_by(BomVersion.version.desc())
    )
    if version is None:
        return None, []
    lines = session.scalars(
        select(BomLine).where(BomLine.bom_version_id == version.id).order_by(BomLine.position)
    ).all()
    return version, list(lines)


def load_rules(session: Session, company_id) -> list[dict]:
    rows = session.scalars(select(CompatibilityRule).where(CompatibilityRule.company_id == company_id)).all()
    if not rows:
        return RULES
    return [{"rule_type": row.rule_type, "constraint": row.constraint_json} for row in rows]


def is_fresh(quote: SupplierQuote, today: date | None = None) -> bool:
    today = today or clock.today()
    if quote.status != "VALID":
        return False
    if quote.valid_until is None or quote.valid_until < today:
        return False
    if quote.unit_price is None or quote.available_qty is None or quote.min_order_qty is None:
        return False
    if quote.shipping_amount is None or quote.taxes_amount is None or quote.estimated_arrival is None:
        return False
    created = quote.created_at.date() if quote.created_at else today
    if (today - created) > timedelta(days=settings.quote_freshness_days):
        return False
    return True


def quote_is_stale_history(session: Session, supplier_id, sku: str) -> bool:
    row = session.scalar(
        select(SupplierQuote.id).where(SupplierQuote.supplier_id == supplier_id, SupplierQuote.sku == sku).limit(1)
    )
    return row is not None


def fresh_quotes_for_task(session: Session, task) -> list[SupplierQuote]:
    rows = session.scalars(
        select(SupplierQuote).where(
            SupplierQuote.company_id == task.company_id,
            (SupplierQuote.task_id == task.id) | (SupplierQuote.task_id.is_(None)),
        )
    ).all()
    return [row for row in rows if is_fresh(row)]


def opt_lines(bom_lines: list[BomLine]) -> list[OptLine]:
    result = []
    for line in bom_lines:
        allowed = list(line.allowed_substitutes_json or [])
        if line.required_sku not in allowed:
            allowed.append(line.required_sku)
        result.append(
            OptLine(
                bom_line_id=str(line.id),
                component_type=line.component_type,
                units_per_computer=line.units_per_computer,
                required_sku=line.required_sku,
                allowed_skus=tuple(allowed),
                position=line.position,
            )
        )
    return result


def opt_quotes(rows: list[SupplierQuote], suppliers: dict[uuid.UUID, Supplier]) -> list[OptQuote]:
    result = []
    for row in rows:
        supplier = suppliers.get(row.supplier_id)
        if supplier is None or not supplier.approved or not supplier.active:
            continue
        if row.component_type is None:
            continue
        result.append(
            OptQuote(
                quote_id=str(row.id),
                supplier_id=str(row.supplier_id),
                supplier_name=supplier.name,
                sku=row.sku,
                component_type=row.component_type,
                unit_price=money(row.unit_price),
                available_qty=int(row.available_qty),
                min_order_qty=int(row.min_order_qty),
                shipping_amount=money(row.shipping_amount) if row.shipping_amount is not None else None,
                taxes_amount=money(row.taxes_amount) if row.taxes_amount is not None else None,
                estimated_arrival=row.estimated_arrival,
                spec=dict(row.spec_json or {}),
            )
        )
    return result


def supplier_map(session: Session, company_id) -> dict:
    rows = session.scalars(select(Supplier).where(Supplier.company_id == company_id)).all()
    return {row.id: row for row in rows}


def open_rfq(session: Session, task_id, supplier_id) -> Rfq | None:
    return session.scalar(
        select(Rfq).where(
            Rfq.task_id == task_id,
            Rfq.supplier_id == supplier_id,
            Rfq.status.in_(("draft", "sent", "needs_review")),
        )
    )


def matching_catalog(session: Session, supplier_id, bom_lines: list[BomLine]) -> list[CatalogItem]:
    allowed: set[str] = set()
    for line in bom_lines:
        allowed.add(line.required_sku)
        allowed.update(line.allowed_substitutes_json or [])
    rows = session.scalars(select(CatalogItem).where(CatalogItem.supplier_id == supplier_id)).all()
    return [row for row in rows if row.sku in allowed]


def latest_plan(session: Session, task_id, statuses: tuple[str, ...] | None = None) -> PurchasePlan | None:
    query = select(PurchasePlan).where(PurchasePlan.task_id == task_id)
    if statuses:
        query = query.where(PurchasePlan.status.in_(statuses))
    return session.scalar(query.order_by(PurchasePlan.version.desc()))


def plan_lines(session: Session, plan_id) -> list[PurchasePlanLine]:
    return list(session.scalars(select(PurchasePlanLine).where(PurchasePlanLine.plan_id == plan_id)).all())


def pending_approval(session: Session, task_id) -> Approval | None:
    return session.scalar(
        select(Approval).where(Approval.task_id == task_id, Approval.status == "pending").order_by(Approval.created_at.desc())
    )
