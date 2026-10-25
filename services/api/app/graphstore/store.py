"""Write and read the stored graph.

Ingest stores vendors, mentions, and claims. A later read keeps a claim only
when every piece of its evidence was already available to this actor.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.graphstore.claims import build_claims, upsert_claims
from app.graphstore.entities import persist_entities
from app.models import ArtifactVersion, Claim, Fact, GraphEpisode, IngestRecord, Mention, ResolvedEntity, SourceArtifact
from app.relationships import load_records
from app.temporal import aware, sha256_text

ORG = "org-decagon"


def index_records(db: Session, organization_id: str, records: list[dict]) -> dict:
    """Upsert entities, mentions, and claims for records already in the database."""
    vendors = {
        row.version_id: row.vendor
        for row in db.scalars(select(IngestRecord)).all()
        if row.version_id
    }
    enriched = []
    for record in records:
        item = dict(record)
        item["vendor"] = vendors.get(record.get("version_id"), "")
        enriched.append(item)
    entities = persist_entities(db, organization_id, enriched)
    mentions = persist_mentions(db, records)
    claims = upsert_claims(db, organization_id, build_claims(records))
    return {"entities": len(entities["entities"]), "mentions": mentions, "claims": len(claims)}


def persist_mentions(db: Session, records: list[dict]) -> int:
    written = 0
    seen: set[tuple[str, str]] = set()
    for record in records:
        version_id = str(record.get("version_id") or "")
        if not version_id:
            continue
        for ref in record.get("references") or []:
            mentioned = str(ref or "").strip()[:80]
            if not mentioned or mentioned == record.get("external_id"):
                continue
            key = (version_id, mentioned)
            if key in seen:
                continue
            seen.add(key)
            existing = db.scalar(
                select(Mention.id).where(
                    Mention.version_id == version_id,
                    Mention.mentioned_external_id == mentioned,
                )
            )
            if existing:
                continue
            db.add(
                Mention(
                    id=sha256_text(f"{version_id}|{mentioned}")[:64],
                    version_id=version_id,
                    mentioned_external_id=mentioned,
                )
            )
            written += 1
    if written:
        db.flush()
    return written


def ensure_company_index(db: Session, organization_id: str = ORG) -> None:
    """Fill the stored graph once. A completed ingest does not run again on its own."""
    versions = _organization_versions(db, organization_id)
    if not versions:
        return
    claim_count = db.scalar(select(func.count()).select_from(Claim).where(Claim.organization_id == organization_id)) or 0
    if not claim_count:
        index_records(db, organization_id, load_records(db, versions))
        db.commit()
    ledger_count = db.scalar(select(func.count()).select_from(Fact).where(Fact.fact_type == "gl_amount")) or 0
    if ledger_count:
        return
    from app.graphstore.ledger import load_ledger_facts, persist_ledger_facts
    from app.ingest.decagon import PACK

    if not PACK.exists():
        return
    persist_ledger_facts(db, organization_id, load_ledger_facts(PACK))
    db.commit()


def edges_from_claims(claims, records: list[dict], as_of: datetime, visible_version_ids: set[str]) -> list[dict]:
    """Turn stored claims into graph edges. Hidden evidence drops the whole claim."""
    moment = aware(as_of)
    by_version = {record["version_id"]: record for record in records}
    by_external: dict[str, dict] = {}
    for record in records:
        by_external.setdefault(record["external_id"], record)
    edges = []
    for claim in claims:
        knowable = aware(_value(claim, "knowable_from"))
        evidence = list(_value(claim, "evidence_version_ids") or [])
        if knowable is None or moment is None or knowable > moment:
            continue
        if any(version_id not in visible_version_ids for version_id in evidence):
            continue
        edges.extend(_claim_edges(claim, evidence, by_version, by_external))
    return edges


def episode_edges(db: Session, records: list[dict], visible_external_ids: set[str]) -> list[dict]:
    """Graphiti suggestions survive only when both ends are already visible."""
    from app.ingest.graphiti_client import accept_links, propose_links

    by_external: dict[str, dict] = {}
    for record in records:
        by_external.setdefault(record["external_id"], record)
    edges = []
    for episode in db.scalars(select(GraphEpisode)).all():
        try:
            body = json.loads(episode.body)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(body, dict):
            continue
        for link in accept_links(propose_links(body), visible_external_ids):
            source = by_external.get(link["from_id"])
            target = by_external.get(link["to_id"])
            if source is None or target is None or source["version_id"] == target["version_id"]:
                continue
            left, right = sorted((source["external_id"], target["external_id"]))
            edges.append(
                {
                    "id": f"epi-{link['claim_type']}-{left}-{right}"[:80],
                    "source": f"artifact:{source['version_id']}",
                    "target": f"artifact:{target['version_id']}",
                    "type": "MENTIONS",
                    "inferred": True,
                    "explanation": link["explanation"],
                    "confidence": "low",
                }
            )
    return edges


def index_counts(db: Session, organization_id: str) -> dict:
    return {
        "entities": db.scalar(select(func.count()).select_from(ResolvedEntity).where(ResolvedEntity.organization_id == organization_id)) or 0,
        "claims": db.scalar(select(func.count()).select_from(Claim).where(Claim.organization_id == organization_id)) or 0,
        "mentions": db.scalar(
            select(func.count())
            .select_from(Mention)
            .join(ArtifactVersion, Mention.version_id == ArtifactVersion.id)
            .join(SourceArtifact, ArtifactVersion.artifact_id == SourceArtifact.id)
            .where(SourceArtifact.organization_id == organization_id)
        )
        or 0,
        "ledger_facts": db.scalar(
            select(func.count())
            .select_from(Fact)
            .join(ArtifactVersion, Fact.artifact_version_id == ArtifactVersion.id)
            .join(SourceArtifact, ArtifactVersion.artifact_id == SourceArtifact.id)
            .where(SourceArtifact.organization_id == organization_id, Fact.fact_type != "amount")
        )
        or 0,
    }


def _organization_versions(db: Session, organization_id: str) -> list[ArtifactVersion]:
    return list(
        db.scalars(
            select(ArtifactVersion)
            .join(SourceArtifact)
            .where(SourceArtifact.organization_id == organization_id)
            .options(selectinload(ArtifactVersion.artifact), selectinload(ArtifactVersion.facts))
        ).all()
    )


def _claim_edges(claim, evidence: list[str], by_version: dict[str, dict], by_external: dict[str, dict]) -> list[dict]:
    claim_type = str(_value(claim, "claim_type") or "")
    status = str(_value(claim, "status") or "")
    explanation = str(_value(claim, "explanation") or "")
    confidence = str(_value(claim, "confidence") or "medium")
    inferred = bool(_value(claim, "is_inferred"))
    if claim_type == "mentions":
        return _pair_edge(_value(claim, "from_node_id"), _value(claim, "to_node_id"), "MENTIONS", explanation, inferred, confidence, by_external)
    if claim_type == "contradicts":
        return _among(evidence, by_version, "CONTRADICTS", explanation, inferred, confidence, differ=True)
    if claim_type == "derived_from":
        edge_type = "AMBIGUOUS" if status == "ambiguous" else "DERIVED_FROM"
        return _from_cost(claim, evidence, by_version, by_external, edge_type, explanation, True, confidence)
    if claim_type == "source_candidate":
        if status == "ambiguous":
            return _from_cost(claim, evidence, by_version, by_external, "AMBIGUOUS", explanation, True, confidence)
        return _pair_edge(_value(claim, "from_node_id"), _value(claim, "to_node_id"), "SOURCE_CANDIDATE", explanation, True, confidence, by_external)
    return []


def _from_cost(claim, evidence, by_version, by_external, edge_type, explanation, inferred, confidence) -> list[dict]:
    cost_id = str(_value(claim, "to_node_id") or _value(claim, "from_node_id") or "")
    cost = by_external.get(cost_id)
    if cost is None:
        return []
    edges = []
    for version_id in evidence:
        other = by_version.get(version_id)
        if other is None or other["version_id"] == cost["version_id"]:
            continue
        edges.extend(_pair_edge(other["external_id"], cost["external_id"], edge_type, explanation, inferred, confidence, by_external))
    return edges


def _among(evidence, by_version, edge_type, explanation, inferred, confidence, *, differ: bool) -> list[dict]:
    docs = []
    seen: set[str] = set()
    for version_id in evidence:
        record = by_version.get(version_id)
        if record is None or record["version_id"] in seen or record.get("amount") is None:
            continue
        seen.add(record["version_id"])
        docs.append(record)
    edges = []
    for index, left in enumerate(docs):
        for right in docs[index + 1 :]:
            same = left["amount"] == right["amount"]
            if differ and same:
                continue
            if not differ and not same:
                continue
            left_id, right_id = sorted((left["external_id"], right["external_id"]))
            edges.append(_edge(left, right, edge_type, explanation, inferred, confidence, f"clm-{edge_type}-{left_id}-{right_id}"))
    return edges


def _pair_edge(source_id, target_id, edge_type, explanation, inferred, confidence, by_external) -> list[dict]:
    source = by_external.get(str(source_id or ""))
    target = by_external.get(str(target_id or ""))
    if source is None or target is None or source["version_id"] == target["version_id"]:
        return []
    left, right = sorted((source["external_id"], target["external_id"]))
    return [_edge(source, target, edge_type, explanation, inferred, confidence, f"clm-{edge_type}-{left}-{right}")]


def _edge(source: dict, target: dict, edge_type: str, explanation: str, inferred: bool, confidence: str, edge_id: str) -> dict:
    return {
        "id": edge_id[:80],
        "source": f"artifact:{source['version_id']}",
        "target": f"artifact:{target['version_id']}",
        "type": edge_type,
        "inferred": inferred,
        "explanation": explanation,
        "confidence": confidence,
    }


def _value(claim, key: str):
    if isinstance(claim, dict):
        return claim.get(key)
    return getattr(claim, key, None)
