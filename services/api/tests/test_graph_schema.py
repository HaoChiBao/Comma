from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import session
from app.main import create_app
from app.models import ArtifactVersion, Claim, EntityAlias, ResolvedEntity
from app.seed import seed


def test_graph_schema_tables_and_uniqueness():
    # Uploads may store a second row for an external id the seed already has, so that pair is indexed, not unique.
    create_app("sqlite:///:memory:")
    with session() as db:
        seed(db)
        inspector = inspect(db.get_bind())
        tables = set(inspector.get_table_names())
        assert {"resolved_entities", "entity_aliases", "mentions", "claims"} <= tables
        _assert_indexes(inspector)

        db.add(
            ResolvedEntity(
                id="re-cloud",
                organization_id="org-acme",
                kind="vendor",
                external_id="vendor-cloud",
                label="Cloud Services Inc.",
            )
        )
        db.flush()
        db.add(EntityAlias(id="alias-1", organization_id="org-acme", entity_id="re-cloud", alias="Cloud Services"))
        db.flush()
        db.add(EntityAlias(id="alias-2", organization_id="org-acme", entity_id="re-cloud", alias="Cloud Services"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        version_id = db.scalar(select(ArtifactVersion.id).limit(1))
        assert version_id
        db.add(_claim("claim-1", "hash-same", version_id))
        db.flush()
        db.add(_claim("claim-2", "hash-same", version_id))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def _assert_indexes(inspector):
    def columns(table, unique=None):
        found = []
        for index in inspector.get_indexes(table):
            if unique is None or bool(index["unique"]) is unique:
                found.append(tuple(index["column_names"]))
        return found

    assert ("content_hash",) in columns("artifact_versions", unique=False)
    assert ("entity_ref", "fact_type", "amount_decimal") in columns("facts", unique=False)
    assert ("version_id",) in columns("mentions")
    assert ("mentioned_external_id",) in columns("mentions")
    assert ("from_node_id", "to_node_id", "claim_type", "knowable_from") in columns("claims", unique=False)

    assert ("organization_id", "external_id") in columns("source_artifacts", unique=False)
    artifact_keys = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("source_artifacts")}
    assert ("organization_id", "external_id") not in artifact_keys


def _claim(claim_id: str, evidence_hash: str, version_id: str) -> Claim:
    return Claim(
        id=claim_id,
        organization_id="org-acme",
        claim_type="supported_by",
        from_node_id="fact:a",
        to_node_id="artifact:b",
        status="exact",
        evidence_version_ids=[version_id],
        formulas=["line_1 + line_2"],
        knowable_from=datetime(2025, 9, 30, tzinfo=timezone.utc),
        evidence_hash=evidence_hash,
        explanation="Same evidence hash must be rejected.",
        is_inferred=False,
        confidence="high",
    )
