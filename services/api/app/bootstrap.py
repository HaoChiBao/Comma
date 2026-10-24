import uuid
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.clock import clock
from app.db import SessionLocal
from app.fixtures.catalog import ACTORS, BETA_ACTOR, RULES, SUPPLIERS
from app.models import (
    Actor,
    CatalogItem,
    Company,
    CompatibilityRule,
    ProcurementTask,
    Supplier,
    SupplierQuote,
)
from app.money import money

NAMESPACE = uuid.UUID("6f9a0b3e-1c4d-4a7e-9b2f-8d5c1a0e7f11")


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def truncate(session: Session) -> None:
    session.execute(
        text(
            """
            TRUNCATE TABLE
                sandbox_deliveries,
                worker_jobs,
                outbox,
                work_events,
                purchase_orders,
                approvals,
                purchase_plan_lines,
                purchase_plans,
                supplier_quotes,
                messages,
                rfqs,
                bom_lines,
                bom_versions,
                procurement_tasks,
                supplier_catalog,
                component_compatibility_rules,
                suppliers,
                actors,
                companies
            RESTART IDENTITY CASCADE
            """
        )
    )


def seed_master(session: Session) -> None:
    now = clock.now()
    acme = Company(
        id=sid("company-acme"),
        name="Acme Computing",
        currency="USD",
        timezone="America/Chicago",
        ship_to="Acme Austin Plant, Austin, TX",
    )
    beta = Company(
        id=sid("company-beta"),
        name="Beta Industrial",
        currency="USD",
        timezone="America/Chicago",
        ship_to="Beta Warehouse, Dallas, TX",
    )
    session.add_all([acme, beta])
    session.flush()
    for actor in ACTORS:
        session.add(
            Actor(
                id=sid(f"actor-{actor['key']}"),
                company_id=acme.id,
                display_name=actor["name"],
                role=actor["role"],
                phone_e164=actor["phone"],
                active=True,
            )
        )
    session.add(
        Actor(
            id=sid("actor-riley"),
            company_id=beta.id,
            display_name=BETA_ACTOR["name"],
            role=BETA_ACTOR["role"],
            phone_e164=BETA_ACTOR["phone"],
            active=True,
        )
    )
    for index, rule in enumerate(RULES):
        for company_id, label in ((acme.id, "acme"), (beta.id, "beta")):
            session.add(
                CompatibilityRule(
                    id=sid(f"rule-{label}-{index}"),
                    company_id=company_id,
                    rule_type=rule["rule_type"],
                    constraint_json=rule["constraint"],
                )
            )
    session.flush()
    helios_id = None
    for entry in SUPPLIERS:
        supplier = Supplier(
            id=sid(f"supplier-{entry['code']}"),
            company_id=acme.id,
            name=entry["name"],
            code=entry["code"],
            contact_email=entry["email"],
            approved=True,
            active=True,
        )
        session.add(supplier)
        session.flush()
        if entry["code"] == "helios":
            helios_id = supplier.id
        for line in entry["lines"]:
            session.add(
                CatalogItem(
                    id=sid(f"catalog-{entry['code']}-{line['sku']}"),
                    supplier_id=supplier.id,
                    sku=line["sku"],
                    component_type=line["component"],
                    spec_json=line["spec"],
                    indicative_price=money(line["price"]),
                    available_qty=line["qty"],
                    min_order_qty=line["moq"],
                    indicative_lead_days=line["lead"],
                    updated_at=now,
                )
            )
    session.add(
        Supplier(
            id=sid("supplier-beta"),
            company_id=beta.id,
            name="Beta Parts",
            code="beta-parts",
            contact_email="parts@beta.sandbox",
            approved=True,
            active=True,
        )
    )
    session.flush()
    gpu = next(line for entry in SUPPLIERS if entry["code"] == "helios" for line in entry["lines"])
    session.add(
        SupplierQuote(
            id=sid("quote-helios-stale"),
            company_id=acme.id,
            supplier_id=helios_id,
            sku=gpu["sku"],
            component_type="gpu",
            unit_price=money(gpu["price"]),
            currency="USD",
            available_qty=gpu["qty"],
            min_order_qty=1,
            shipping_amount=money(gpu["shipping"]),
            taxes_amount=money("0.00"),
            estimated_arrival=date(2026, 9, 1),
            valid_until=date(2026, 10, 1),
            evidence_ref="historical-price-file",
            status="EXPIRED",
            version=1,
            spec_json=gpu["spec"],
            uncertainty_json=[],
            created_at=now - timedelta(days=40),
        )
    )
    session.add(
        ProcurementTask(
            id=sid("task-beta"),
            company_id=beta.id,
            created_by=sid("actor-riley"),
            public_code="BETA-1001",
            item_spec_json={"product": "desktop", "note": "tenant fixture"},
            quantity=5,
            budget_amount=money("10000.00"),
            currency="USD",
            delivery_deadline=date(2026, 12, 1),
            destination="Beta Warehouse, Dallas, TX",
            status="NEEDS_INFO",
            version=1,
            preferences_json={},
            assumptions_json=[],
            blocked_reason="fixture task for tenant isolation",
            created_at=now,
            updated_at=now,
        )
    )


def ensure_actors(session: Session) -> None:
    """Add organization members that were introduced after the first seed."""
    acme = session.get(Company, sid("company-acme"))
    if acme is None:
        return
    for actor in ACTORS:
        found = session.scalar(select(Actor).where(Actor.phone_e164 == actor["phone"]))
        if found is not None:
            continue
        session.add(
            Actor(
                id=sid(f"actor-{actor['key']}"),
                company_id=acme.id,
                display_name=actor["name"],
                role=actor["role"],
                phone_e164=actor["phone"],
                active=True,
            )
        )


def seed(*, reset: bool = False, include_demo_task: bool = True) -> None:
    session = SessionLocal()
    try:
        # Transaction-scoped so a failed seed cannot leave a session lock on a pooled connection.
        session.execute(text("SELECT pg_advisory_xact_lock(814204)"))
        if reset:
            truncate(session)
        else:
            existing = session.get(Company, sid("company-acme"))
            if existing is not None:
                ensure_actors(session)
                session.commit()
                return
        seed_master(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if include_demo_task:
        from app.pipeline import demo_seed_message, handle_inbound_text

        handle_session = SessionLocal()
        try:
            handle_inbound_text(handle_session, demo_seed_message())
        finally:
            handle_session.close()


def company_is_seeded() -> bool:
    session = SessionLocal()
    try:
        return session.get(Company, sid("company-acme")) is not None
    finally:
        session.close()
