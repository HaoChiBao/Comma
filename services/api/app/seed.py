from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccessGrant, Actor, ArtifactVersion, Case, Fact, GraphEdge, Organization, SourceArtifact
from app.seed_data import JORDAN, MAYA, ORG, ORG_BETA, RILEY, world
from app.temporal import utc


def seed(db: Session) -> None:
    if db.scalar(select(Organization.id).limit(1)):
        return
    db.add_all(
        [
            Organization(id=ORG, name="Acme Corp"),
            Organization(id=ORG_BETA, name="Beta Industrial"),
        ]
    )
    db.flush()
    db.add_all(
        [
            Actor(id=MAYA, organization_id=ORG, name="Maya Chen", role="accountant"),
            Actor(id=JORDAN, organization_id=ORG, name="Jordan Hale", role="auditor"),
            Actor(id=RILEY, organization_id=ORG_BETA, name="Riley Quinn", role="accountant"),
        ]
    )
    db.flush()
    versions, cases = world()
    _add_versions(db, versions)
    for case in cases:
        _add_case(db, case)
    db.commit()


def _add_versions(db: Session, versions: list[dict]) -> None:
    artifacts: dict[str, SourceArtifact] = {}
    now = datetime.now(timezone.utc)
    for item in versions:
        artifact = artifacts.get(item["artifact_id"])
        if artifact is None:
            artifact = SourceArtifact(
                id=item["artifact_id"],
                organization_id=item["organization_id"],
                kind=item["kind"],
                source_system=item["source_system"],
                external_id=item["external_id"],
                title=item["title"],
            )
            artifacts[item["artifact_id"]] = artifact
            db.add(artifact)
        version = ArtifactVersion(
            id=item["version_id"],
            artifact_id=item["artifact_id"],
            version=int(item["version_id"].rsplit(":v", 1)[1]),
            content_text=item["content_text"],
            content_hash=item["content_hash"],
            document_date=item["document_date"],
            business_valid_from=item["business_valid_from"],
            business_valid_to=item["business_valid_to"],
            available_from=item["available_from"],
            available_to=item["available_to"],
            ingested_at=item["ingested_at"],
            availability_status=item["availability_status"],
            availability_evidence=item["availability_evidence"],
            metadata_json={
                "case_ids": item["case_ids"],
                "entity_ref": item["entity_ref"],
                "formula": item["formula"],
                "period": item["period"],
            },
        )
        db.add(version)
        for role, start, end in item["grants"]:
            db.add(
                AccessGrant(
                    id=f"grant-{item['version_id']}-{role}",
                    artifact_version_id=item["version_id"],
                    role=role,
                    grant_from=start if hasattr(start, "tzinfo") else utc(start),
                    grant_to=end if end is None or hasattr(end, "tzinfo") else utc(end),
                    permission="read",
                )
            )
        if item["amount"] is not None or item["fact_type"] != "amount":
            db.add(
                Fact(
                    id=f"fact-{item['version_id']}",
                    artifact_version_id=item["version_id"],
                    fact_type=item["fact_type"],
                    entity_ref=item["entity_ref"],
                    amount_decimal=item["amount"],
                    currency=item["currency"],
                    value_text=item["content_text"][:240],
                    source_locator=item["locator"],
                    extraction_status="seeded",
                )
            )
        for fact_id, amount, locator in item["extra_facts"]:
            db.add(
                Fact(
                    id=fact_id,
                    artifact_version_id=item["version_id"],
                    fact_type="line_amount",
                    entity_ref=item["entity_ref"],
                    amount_decimal=Decimal(amount),
                    currency=item["currency"],
                    value_text=f"{locator} {amount}",
                    source_locator=locator,
                    extraction_status="seeded",
                )
            )
        if item["formula"]:
            unit = Fact(
                id="fact-po220-unit",
                artifact_version_id=item["version_id"],
                fact_type="unit_price",
                entity_ref=item["entity_ref"],
                amount_decimal=item["amount"] and Decimal("10.00"),
                currency="USD",
                value_text="10.00",
                source_locator="unit price",
                extraction_status="seeded",
            )
            qty = Fact(
                id="fact-po220-qty",
                artifact_version_id=item["version_id"],
                fact_type="quantity",
                entity_ref=item["entity_ref"],
                amount_decimal=Decimal("8"),
                currency="USD",
                value_text="8",
                source_locator="quantity",
                extraction_status="seeded",
            )
            db.add_all([unit, qty])
            db.add(
                GraphEdge(
                    id="edge-po220-derived-unit",
                    from_node_id="fact:fact-po220-unit",
                    to_node_id="artifact:PO-220:v1",
                    edge_type="DERIVED_FROM",
                    supporting_version_id=item["version_id"],
                    confidence="high",
                    is_inferred=False,
                    explanation="estimated total = unit_price * estimated_quantity",
                    created_at=now,
                )
            )
            db.add(
                GraphEdge(
                    id="edge-po220-derived-qty",
                    from_node_id="fact:fact-po220-qty",
                    to_node_id="artifact:PO-220:v1",
                    edge_type="DERIVED_FROM",
                    supporting_version_id=item["version_id"],
                    confidence="high",
                    is_inferred=False,
                    explanation="estimated total = unit_price * estimated_quantity",
                    created_at=now,
                )
            )


def _add_case(db: Session, case: tuple) -> None:
    case_id, case_type, artifact_id, version_id, summary, entity, amount, probes = case
    db.add(
        Case(
            id=case_id,
            organization_id=ORG,
            case_type=case_type,
            decision_artifact_id=artifact_id,
            decision_version_id=version_id,
            decision_at=utc("2025-09-30T23:59:00+00:00"),
            summary=summary,
            status="open",
            vendor_name=_vendor(case_id),
            entity_ref=entity,
            recorded_amount=Decimal(amount),
            probe_version_ids=probes,
        )
    )


def _vendor(case_id: str) -> str:
    return {
        "CASE-001": "Cloud Services Inc.",
        "CASE-002": "Harbor Logistics",
        "CASE-003": "Northwind Hosting",
        "CASE-004": "Northwind Hosting",
        "CASE-005": "Cloud Services Inc.",
        "CASE-006": "Harbor Logistics",
        "CASE-007": "Cloud Services Inc.",
        "CASE-008": "Apex Freight",
        "CASE-009": "Cloud Services Inc.",
        "CASE-010": "Cloud Services Inc.",
    }[case_id]


def reset(db: Session) -> None:
    from app.models import Base
    from app.db import engine

    Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    seed(db)


if __name__ == "__main__":
    import os

    from app.db import configure, session

    configure(os.environ.get("DATABASE_URL", "sqlite:///./worldstate.db"))
    with session() as db:
        if os.environ.get("SEED_RESET") == "1":
            reset(db)
        else:
            seed(db)
        print("seeded", db.query(ArtifactVersion).count(), "versions")
