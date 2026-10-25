"""Write and read the stored graph.

Ingest stores vendors, mentions, and claims. A later read keeps a claim only
when every piece of its evidence was already available to this actor.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.graphstore.claims import build_claims, upsert_claims
from app.graphstore.entities import persist_entities, resolve_document, transaction_external_id, transaction_ids_for
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


def structure_graph(db: Session, organization_id: str, records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Vendors, transactions, mentions, and ledger rows for documents already in view.

    Amounts stay off these links. A document that is not in `records` is not copied in.
    """
    version_ids = [str(record.get("version_id") or "") for record in records if record.get("version_id")]
    if not version_ids:
        return [], []
    vendors = {
        row.version_id: row.vendor
        for row in db.scalars(select(IngestRecord).where(IngestRecord.version_id.in_(version_ids))).all()
    }
    mentions = [
        {"version_id": row.version_id, "mentioned_external_id": row.mentioned_external_id}
        for row in db.scalars(select(Mention).where(Mention.version_id.in_(version_ids))).all()
    ]
    facts = [
        {
            "version_id": row.artifact_version_id,
            "fact_type": row.fact_type,
            "entity_ref": row.entity_ref,
            "source_locator": row.source_locator,
        }
        for row in db.scalars(select(Fact).where(Fact.artifact_version_id.in_(version_ids), Fact.fact_type != "amount")).all()
    ]
    enriched = []
    for record in records:
        item = dict(record)
        item["vendor"] = vendors.get(record.get("version_id"), record.get("vendor") or "")
        enriched.append(item)
    return connect_records(organization_id, enriched, mentions, facts)


def connect_records(organization_id: str, records: list[dict], mentions: list[dict], facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build entity hubs and every stored link among the records passed in.

    No amount is read from a fact or a hidden document. Locators are kept in full.
    """
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    by_external = {str(record.get("external_id") or ""): record for record in records if record.get("external_id")}
    by_version = {str(record.get("version_id") or ""): record for record in records if record.get("version_id")}

    def artifact(record: dict) -> str:
        return f"artifact:{record['version_id']}"

    def entity_node(spec: dict) -> str:
        node_id = f"entity:{spec['id']}"
        if node_id not in nodes:
            kind = "VENDOR" if spec.get("kind") == "vendor" else "TRANSACTION"
            nodes[node_id] = _entity_node(node_id, kind, spec.get("label") or spec.get("external_id") or node_id)
        return node_id

    def add_edge(source: str, target: str, edge_type: str, explanation: str, *, inferred: bool) -> None:
        if not source or not target or source == target:
            return
        key = (edge_type, source, target)
        current = edges.get(key)
        if current is None:
            edges[key] = {
                "id": f"ln-{sha256_text(f'{edge_type}|{source}|{target}')[:24]}",
                "source": source,
                "target": target,
                "type": edge_type,
                "inferred": inferred,
                "explanation": explanation,
                "confidence": "high" if not inferred else "medium",
            }
            return
        if explanation and explanation not in current["explanation"]:
            current["explanation"] = f"{current['explanation']} {explanation}"

    for record in records:
        external_id = str(record.get("external_id") or "")
        version_id = str(record.get("version_id") or "")
        if not external_id or not version_id:
            continue
        resolved = resolve_document(
            organization_id,
            external_id,
            str(record.get("kind") or ""),
            record.get("vendor") or "",
            list(record.get("references") or []),
        )
        source = artifact(record)
        if resolved.get("transaction"):
            add_edge(
                source,
                entity_node(resolved["transaction"]),
                "ATTACHED",
                f"{external_id} is this transaction.",
                inferred=False,
            )
        if resolved.get("vendor"):
            add_edge(
                source,
                entity_node(resolved["vendor"]),
                "FILED_WITH",
                f"{external_id} names {resolved['vendor'].get('label') or 'this vendor'}.",
                inferred=False,
            )
        own = transaction_external_id(external_id)
        for txn_id in sorted(transaction_ids_for(external_id, list(record.get("references") or []))):
            if txn_id == own:
                continue
            spec = resolve_document(organization_id, txn_id, "transaction", None, [])["transaction"]
            if spec is None:
                continue
            target = by_external.get(txn_id)
            if target is not None and str(target.get("version_id") or "") != version_id:
                add_edge(source, artifact(target), "CITES", f"{external_id} names {txn_id}.", inferred=False)
            add_edge(source, entity_node(spec), "CITES", f"{external_id} names {txn_id}.", inferred=False)

    for mention in mentions:
        source_record = by_version.get(str(mention.get("version_id") or ""))
        mentioned = str(mention.get("mentioned_external_id") or "").strip()
        if source_record is None or not mentioned or mentioned == source_record.get("external_id"):
            continue
        source = artifact(source_record)
        target_record = by_external.get(mentioned)
        if target_record is not None and str(target_record.get("version_id") or "") != source_record.get("version_id"):
            add_edge(source, artifact(target_record), "MENTIONS", f"{source_record['external_id']} mentions {mentioned}.", inferred=False)
        spec = resolve_document(organization_id, mentioned, "transaction", None, [])["transaction"]
        if spec is not None:
            add_edge(source, entity_node(spec), "MENTIONS", f"{source_record['external_id']} mentions {mentioned}.", inferred=False)

    ledger: dict[tuple[str, str, str], list[str]] = {}
    for fact in facts:
        source_record = by_version.get(str(fact.get("version_id") or ""))
        fact_type = str(fact.get("fact_type") or "").strip()
        entity_ref = str(fact.get("entity_ref") or "").strip()
        locator = str(fact.get("source_locator") or "").strip()
        if source_record is None or not fact_type or not entity_ref or not locator:
            continue
        ledger.setdefault((str(source_record["version_id"]), entity_ref, fact_type), []).append(locator)
    for (version_id, entity_ref, fact_type), locators in ledger.items():
        source_record = by_version[version_id]
        source = artifact(source_record)
        unique = sorted({_without_amounts(locator) for locator in locators})
        unique = [item for item in unique if item]
        explanation = f"{fact_type}: " + "; ".join(unique)
        target_record = by_external.get(entity_ref)
        if target_record is not None and str(target_record.get("version_id") or "") != version_id:
            add_edge(source, artifact(target_record), "LEDGER", explanation, inferred=False)
        spec = resolve_document(organization_id, entity_ref, "transaction", None, [])["transaction"]
        if spec is not None:
            add_edge(source, entity_node(spec), "LEDGER", explanation, inferred=True)

    ordered_nodes = [nodes[key] for key in sorted(nodes)]
    ordered_edges = [edges[key] for key in sorted(edges)]
    return ordered_nodes, ordered_edges


def _without_amounts(text: str) -> str:
    return " ".join(re.sub(r"\d{1,3}(?:,\d{3})*\.\d{2}", "", text).split())


def _entity_node(node_id: str, kind: str, label: str) -> dict:
    group = "Vendors" if kind == "VENDOR" else "Transactions"
    return {
        "id": node_id,
        "type": kind,
        "label": label,
        "title": label,
        "amount": None,
        "group": group,
        "case_id": "",
        "origin": "entity",
        "version_id": "",
        "available_from": None,
        "document_date": None,
        "redacted": False,
        "text": "",
    }
