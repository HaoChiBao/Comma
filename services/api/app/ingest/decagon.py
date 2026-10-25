import csv
import io
import os
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.graphiti_client import add_episode
from app.ingest.parse import extract_text, parse_document
from app.models import (
    AccessGrant,
    Actor,
    ArtifactVersion,
    Case,
    Fact,
    GraphEdge,
    IngestDocument,
    IngestJob,
    IngestRecord,
    IngestStep,
    Organization,
    SourceArtifact,
)
from app.temporal import aware, money, sha256_text, utc

ORG = "org-decagon"
PACK = Path(os.environ.get("DECAGON_PACK") or Path(__file__).resolve().parents[2] / "data" / "decagon-agent-safe.zip")
ROOT = os.environ.get("DECAGON_ROOT") or "Decagon_WorldState_Synthetic_Dataset/"
CHUNK = 40
ACTORS = (
    ("actor-decagon-finance", "Avery Lin", "accountant"),
    ("actor-decagon-auditor", "Noah Patel", "auditor"),
    ("actor-decagon-legal", "Camille Ortiz", "legal"),
)
KIND = {
    "INVOICE": "invoice",
    "CUSTOMER_INVOICE": "invoice",
    "PURCHASE_ORDER": "purchase_order",
    "ORDER_FORM": "purchase_order",
    "EMAIL": "email",
    "SERVICE_CONFIRMATION": "memo",
    "CONTRACT": "contract",
    "CONTRACT_AMENDMENT": "contract",
    "POLICY": "policy",
    "REMITTANCE": "memo",
    "CREDIT_MEMO": "memo",
    "ACCRUAL_DECISION": "journal",
    "CORRECTIVE_JOURNAL": "journal",
    "JOURNAL": "journal",
}
_CANONICAL_RANK = {
    "INVOICE": 0,
    "CUSTOMER_INVOICE": 1,
    "PURCHASE_ORDER": 2,
    "ORDER_FORM": 3,
    "CONTRACT": 4,
    "CONTRACT_AMENDMENT": 5,
    "CREDIT_MEMO": 6,
    "USAGE_REPORT": 7,
    "BANK_FEED": 8,
    "SERVICE_CONFIRMATION": 9,
    "POLICY": 10,
    "REMITTANCE": 11,
    "ACCRUAL_WORKPAPER": 20,
    "FINANCE_WORKPAPER": 21,
    "RECONCILIATION": 30,
    "APPROVAL": 40,
    "REVIEW_PACKET": 50,
    "EMAIL": 90,
}
SCOPE_ROLE = {"finance": "accountant", "operations": "accountant", "controller": "auditor", "legal": "legal"}


def ensure_company(db: Session) -> None:
    if db.get(Organization, ORG) is None:
        db.add(Organization(id=ORG, name="Decagon (fictional finance simulation)"))
        db.flush()
    for actor_id, name, role in ACTORS:
        if db.get(Actor, actor_id) is None:
            db.add(Actor(id=actor_id, organization_id=ORG, name=name, role=role))
    db.commit()


def start_decagon(db: Session) -> IngestJob:
    if not PACK.exists():
        raise ValueError("The Decagon company document pack is not installed.")
    ensure_company(db)
    current = db.scalars(select(IngestJob).where(IngestJob.organization_id == ORG).order_by(IngestJob.created_at.desc())).first()
    if current is not None and current.status == "completed":
        return current
    if current is not None and current.status in {"queued", "running", "paused", "failed"}:
        if current.status != "running":
            from app.ingest.service import change_status

            return change_status(db, current.id, "running")
        return current
    now = datetime.now(timezone.utc)
    job = IngestJob(
        id=uuid4().hex[:16],
        organization_id=ORG,
        status="queued",
        available_from=utc("2025-09-30T00:00:00+00:00"),
        error="",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    db.add(
        IngestStep(
            id=uuid4().hex[:16],
            job_id=job.id,
            sequence=1,
            name="catalog",
            status="pending",
            detail_json={"label": "Catalog the company documents"},
            error="",
        )
    )
    db.commit()
    from app.ingest.service import launch

    launch(job.id)
    db.refresh(job)
    return job


def catalog(db: Session, job: IngestJob, step: IngestStep) -> dict:
    rows = sorted(_manifest(), key=lambda row: row["path"])
    already = db.scalars(select(IngestStep).where(IngestStep.job_id == job.id, IngestStep.name == "absorb")).all()
    if not already:
        for index in range(0, len(rows), CHUNK):
            chunk = rows[index : index + CHUNK]
            _add_step(
                db,
                job.id,
                10 + index // CHUNK,
                "absorb",
                {"label": f"Read documents {index + 1}–{index + len(chunk)}", "paths": [row["path"] for row in chunk]},
            )
        _add_step(db, job.id, 800, "decisions", {"label": "Record accruals and corrections"})
        _add_step(db, job.id, 900, "relate", {"label": "Find relationships by reading the documents"})
        _add_step(db, job.id, 1000, "open_cases", {"label": "Open the ten investigations"})
        _add_step(db, job.id, 1100, "analyze", {"label": "Analyze what each snapshot can support"})
        _add_step(db, job.id, 1200, "reconcile", {"label": "Confirm every source was recorded"})
    return {"documents": len(rows)}


def absorb(db: Session, job: IngestJob, step: IngestStep) -> dict:
    paths = list((step.detail_json or {}).get("paths") or [])
    manifest = {row["path"]: row for row in _manifest()}
    identity = _identities(list(manifest.values()))
    amounts = _graph_amounts()
    seen = {record.external_id for record in db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()}
    written = 0
    with zipfile.ZipFile(PACK) as archive:
        for index, path in enumerate(paths):
            row = manifest[path]
            external_id, extra_refs = identity[path]
            if external_id in seen:
                continue
            payload = archive.read(ROOT + path)
            filename = path.rsplit("/", 1)[-1]
            try:
                text = extract_text(filename, _media(filename), payload)
            except Exception:
                text = ""
            if not text.strip():
                text = f"{row['source_type']} {row['entity_id']}"
            try:
                parsed = parse_document(text, utc(row["available_at"]))
                chosen = next((item for item in parsed if item["external_id"] == external_id), parsed[0])
            except Exception:
                chosen = {"vendor": "", "amount": None, "references": [], "formula": ""}
            amount = chosen["amount"]
            if external_id == row["entity_id"] and amounts.get(row["entity_id"]):
                amount = Decimal(amounts[row["entity_id"]])
            references = list(dict.fromkeys([*chosen["references"], *extra_refs]))
            _persist(
                db,
                job,
                filename=filename,
                text=text,
                external_id=external_id,
                kind=KIND.get(row["source_type"], "memo"),
                vendor=chosen["vendor"],
                amount=amount,
                document_date=_day(row["document_date"], row["available_at"]),
                available_from=utc(row["available_at"]),
                case_id=row["case_id"],
                entity_ref=(row["case_id"] or external_id).lower(),
                references=references,
                scopes=row["actor_scope"],
                source_type=row["source_type"],
                formula=chosen.get("formula") or "",
            )
            seen.add(external_id)
            written += 1
            detail = dict(step.detail_json or {})
            detail["done"] = index + 1
            step.detail_json = detail
            db.commit()
    return {"written": written}


def decisions(db: Session, job: IngestJob, step: IngestStep) -> dict:
    seen = {record.external_id for record in db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()}
    written = 0
    for node in _graph_nodes():
        if node["kind"] not in {"ACCRUAL_DECISION", "CORRECTIVE_JOURNAL"} or node["id"] in seen:
            continue
        when = utc(node["available_at"])
        amount = Decimal(node["amount_usd"]) if node["amount_usd"] else None
        label = "Accrual" if node["kind"] == "ACCRUAL_DECISION" else "Corrective journal"
        text = (
            f"{label} {node['id']}\n"
            f"Vendor: case {node['case_id']}\n"
            f"Recorded at: {node['available_at']}\n"
            f"Amount: ${money(amount)}\n"
            "No formula is preserved for this entry."
        )
        _persist(
            db,
            job,
            filename=f"{node['id']}.txt",
            text=text,
            external_id=node["id"],
            kind="journal",
            vendor=node["case_id"],
            amount=amount,
            document_date=when,
            available_from=when,
            case_id=node["case_id"],
            entity_ref=node["case_id"].lower(),
            references=[node["id"]],
            scopes="finance,operations,controller",
            source_type=node["kind"],
        )
        written += 1
    written += _record_v2_journals(db, job, seen)
    return {"decisions": written}


def relate(db: Session, job: IngestJob, step: IngestStep) -> dict:
    from app.relationships import find_relationships, load_records

    versions = [
        version
        for version in (db.get(ArtifactVersion, version_id) for version_id in _versions_by_external_id(db, job.id).values())
        if version is not None
    ]
    linked = 0
    for edge in find_relationships(load_records(db, versions)):
        if db.get(GraphEdge, edge["id"]) is None:
            db.add(
                GraphEdge(
                    id=edge["id"],
                    from_node_id=edge["source"],
                    to_node_id=edge["target"],
                    edge_type=edge["type"],
                    supporting_version_id=edge["source"].removeprefix("artifact:"),
                    confidence=edge["confidence"],
                    is_inferred=edge["inferred"],
                    explanation=edge["explanation"],
                    created_at=datetime.now(timezone.utc),
                )
            )
            linked += 1
    from app.graphstore.store import index_records

    indexed = index_records(db, ORG, load_records(db, versions))
    return {"edges": linked, **indexed}


def open_cases(db: Session, job: IngestJob, step: IngestStep) -> dict:
    records = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()
    grouped: dict[str, list[tuple[IngestRecord, ArtifactVersion]]] = {}
    for record in records:
        version = db.get(ArtifactVersion, record.version_id)
        if version is None:
            continue
        for case_id in (version.metadata_json or {}).get("case_ids") or []:
            grouped.setdefault(case_id, []).append((record, version))
    opened = []
    for row in _registry():
        group = grouped.get(row["case_id"], [])
        if not group:
            continue
        decision = next((pair for pair in group if pair[0].kind == "journal" and pair[0].external_id.startswith("ACR-")), group[0])
        vendor = next((pair[0].vendor for pair in group if pair[0].kind == "purchase_order" and pair[0].vendor), "")
        if not vendor:
            vendor = next((pair[0].vendor for pair in group if pair[0].vendor and not pair[0].vendor.startswith("C")), row["vendor_id"])
        case_id = row["case_id"]
        probe = [pair[1].id for pair in group]
        policy = _policy_version(db, "POL-ACC-v1")
        if policy is not None and policy not in probe:
            probe.append(policy)
            _tag_case(db, policy, case_id)
        if db.get(Case, case_id) is None:
            db.add(
                Case(
                    id=case_id,
                    organization_id=ORG,
                    case_type="accrual_investigation",
                    decision_artifact_id=_artifact_id(decision[1].id),
                    decision_version_id=decision[1].id,
                    decision_at=decision[0].available_from,
                    summary=f"{row['title']}. Related documents appear on the graph only after each one was available.",
                    status="open",
                    vendor_name=vendor,
                    entity_ref=row["case_id"].lower(),
                    recorded_amount=decision[0].amount or Decimal("0"),
                    probe_version_ids=probe,
                )
            )
        opened.append(case_id)
    for row in _optional_csv("data_extensions/v2_case_registry.csv"):
        group = grouped.get(row["case_id"], [])
        if not group:
            continue
        decision = next((pair for pair in group if pair[0].external_id == row["primary_record"]), None)
        if decision is None:
            continue
        vendor = next((pair[0].vendor for pair in group if pair[0].kind in {"purchase_order", "contract"} and pair[0].vendor), "")
        if not vendor:
            vendor = row["title"]
        case_id = row["case_id"]
        probe = [pair[1].id for pair in group]
        policy_name = "POL-ACC-v2" if aware(decision[0].available_from) >= utc("2025-10-01T00:00:00+00:00") else "POL-ACC-v1"
        policy = _policy_version(db, policy_name)
        if policy is not None and policy not in probe:
            probe.append(policy)
            _tag_case(db, policy, case_id)
        if db.get(Case, case_id) is None:
            db.add(
                Case(
                    id=case_id,
                    organization_id=ORG,
                    case_type="accrual_investigation",
                    decision_artifact_id=_artifact_id(decision[1].id),
                    decision_version_id=decision[1].id,
                    decision_at=decision[0].available_from,
                    summary=f"{row['title']}. Related documents appear on the graph only after each one was available.",
                    status="open",
                    vendor_name=vendor,
                    entity_ref=row["case_id"].lower(),
                    recorded_amount=decision[0].amount or Decimal("0"),
                    probe_version_ids=probe,
                )
            )
        opened.append(case_id)
    return {"cases": opened}


def analyze(db: Session, job: IngestJob, step: IngestStep) -> dict:
    from app.investigator import run_investigation
    from app.snapshots import create_snapshot

    needed = [node["id"] for node in _graph_nodes() if not node["id"].startswith("POLICY-CONTEXT")]
    needed.extend(row["primary_record"] for row in _optional_csv("data_extensions/v2_case_registry.csv"))
    have = {record.external_id for record in db.scalars(select(IngestRecord).where(IngestRecord.job_id == job.id)).all()}
    missing = [item for item in needed if item not in have]
    if missing:
        raise ValueError("Case evidence was not recorded: " + ", ".join(missing))
    findings = []
    moment = utc("2025-12-15T12:00:00+00:00")
    for row in [*_registry(), *_optional_csv("data_extensions/v2_case_registry.csv")]:
        if db.get(Case, row["case_id"]) is None:
            raise ValueError(f"{row['case_id']} was not opened.")
        snapshot = create_snapshot(db, case_id=row["case_id"], actor_id="actor-decagon-auditor", as_of=moment, mode="audit")
        investigation = run_investigation(db, snapshot.id)
        findings.append({"case_id": row["case_id"], "finding": (investigation.finding_json or {}).get("finding")})
    return {"findings": findings}


HANDLERS = {
    "catalog": catalog,
    "absorb": absorb,
    "decisions": decisions,
    "relate": relate,
    "open_cases": open_cases,
    "analyze": analyze,
}


def _persist(
    db: Session,
    job: IngestJob,
    *,
    filename: str,
    text: str,
    external_id: str,
    kind: str,
    vendor: str,
    amount: Decimal | None,
    document_date: datetime,
    available_from: datetime,
    case_id: str,
    entity_ref: str,
    references: list,
    scopes: str,
    source_type: str,
    formula: str = "",
) -> None:
    document = IngestDocument(
        id=uuid4().hex[:16],
        job_id=job.id,
        filename=filename[:300],
        media_type=_media(filename),
        payload=b"",
        text=text,
        content_hash=sha256_text(text),
    )
    db.add(document)
    db.flush()
    record = IngestRecord(
        id=uuid4().hex[:16],
        job_id=job.id,
        document_id=document.id,
        external_id=external_id[:80],
        kind=kind,
        vendor=(vendor or "")[:200],
        entity_ref=entity_ref[:80],
        references_json=references,
        amount=amount,
        currency="USD",
        document_date=document_date,
        available_from=available_from,
        text=text,
        formula=formula,
    )
    db.add(record)
    db.flush()
    version_id = "d" + sha256_text(external_id)[:16]
    artifact_id = "a" + sha256_text(external_id)[:16]
    business_from, business_to = _policy_window(external_id)
    db.add(
        SourceArtifact(
            id=artifact_id,
            organization_id=ORG,
            kind=kind if kind in {"purchase_order", "email", "invoice", "journal", "policy", "contract", "memo"} else "memo",
            source_system="decagon-corpus",
            external_id=external_id[:64],
            title=f"{source_type.replace('_', ' ').title()} {external_id}"[:300],
        )
    )
    db.flush()
    db.add(
        ArtifactVersion(
            id=version_id,
            artifact_id=artifact_id,
            version=1,
            content_text=text,
            content_hash=sha256_text(text),
            document_date=document_date,
            business_valid_from=business_from,
            business_valid_to=business_to,
            available_from=available_from,
            available_to=None,
            ingested_at=datetime.now(timezone.utc),
            availability_status="verified",
            availability_evidence="Availability comes from the company source manifest. The printed date is not availability.",
            metadata_json={"case_ids": [case_id] if case_id else [], "entity_ref": entity_ref, "formula": formula, "job_id": job.id},
        )
    )
    db.flush()
    for role in _roles(scopes):
        db.add(
            AccessGrant(
                id=f"g{version_id}{role}"[:80],
                artifact_version_id=version_id,
                role=role,
                grant_from=available_from,
                grant_to=None,
                permission="read",
            )
        )
    if amount is not None:
        db.add(
            Fact(
                id=f"f{version_id}amt"[:80],
                artifact_version_id=version_id,
                fact_type="amount",
                entity_ref=entity_ref[:80],
                amount_decimal=amount,
                currency="USD",
                value_text=money(amount) or "",
                source_locator="amount",
                extraction_status="extracted",
            )
        )
    record.version_id = version_id
    result = add_episode(db, record, job.id, ORG.replace("-", "_"))
    record.episode_id = result["episode_id"]


def _roles(scopes: str) -> list[str]:
    roles = []
    for scope in scopes.split(","):
        role = SCOPE_ROLE.get(scope.strip())
        if role and role not in roles:
            roles.append(role)
    return roles or ["accountant", "auditor"]


def _policy_window(external_id: str) -> tuple[datetime | None, datetime | None]:
    if external_id == "POL-ACC-v1":
        return utc("2025-09-01T00:00:00+00:00"), utc("2025-10-01T00:00:00+00:00")
    if external_id == "POL-ACC-v2":
        return utc("2025-10-01T00:00:00+00:00"), None
    return None, None


def _policy_version(db: Session, external_id: str) -> str | None:
    artifact = db.scalar(select(SourceArtifact).where(SourceArtifact.organization_id == ORG, SourceArtifact.external_id == external_id))
    if artifact is None:
        return None
    version = db.scalar(select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id))
    return None if version is None else version.id


def _tag_case(db: Session, version_id: str, case_id: str) -> None:
    version = db.get(ArtifactVersion, version_id)
    if version is None:
        return
    metadata = dict(version.metadata_json or {})
    metadata["case_ids"] = sorted(set(metadata.get("case_ids") or []) | {case_id})
    version.metadata_json = metadata


def _artifact_id(version_id: str) -> str:
    return "a" + version_id[1:]


def _versions_by_external_id(db: Session, job_id: str) -> dict[str, str]:
    rows = db.scalars(select(IngestRecord).where(IngestRecord.job_id == job_id)).all()
    return {row.external_id: row.version_id for row in rows if row.version_id}


def _day(document_date: str, available_at: str) -> datetime:
    if document_date:
        return utc(document_date + "T00:00:00+00:00")
    return utc(available_at)


def _media(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".eml"):
        return "message/rfc822"
    if name.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith(".csv"):
        return "text/csv"
    return "text/plain"


def _add_step(db: Session, job_id: str, sequence: int, name: str, detail: dict) -> None:
    db.add(
        IngestStep(
            id=uuid4().hex[:16],
            job_id=job_id,
            sequence=sequence,
            name=name,
            status="pending",
            detail_json=detail,
            error="",
        )
    )


def _read(name: str) -> str:
    with zipfile.ZipFile(PACK) as archive:
        return archive.read(ROOT + name).decode("utf-8")


def _manifest() -> list[dict]:
    return list(csv.DictReader(io.StringIO(_read("metadata/source_manifest.csv"))))


def _registry() -> list[dict]:
    return list(csv.DictReader(io.StringIO(_read("metadata/case_registry.csv"))))


def _graph_nodes() -> list[dict]:
    import json

    return [json.loads(line) for line in _read("graph/graph_nodes.jsonl").splitlines() if line.strip()]


def _graph_amounts() -> dict[str, str]:
    return {node["id"]: node.get("amount_usd") or "" for node in _graph_nodes()}


def _optional_csv(name: str) -> list[dict]:
    try:
        return list(csv.DictReader(io.StringIO(_read(name))))
    except KeyError:
        return []


def _journal_ids() -> set[str]:
    ids = {node["id"] for node in _graph_nodes() if node["kind"] in {"ACCRUAL_DECISION", "CORRECTIVE_JOURNAL"}}
    ids.update(row["journal_id"] for row in _optional_csv("data_extensions/v2_journal_lines.csv") if row.get("journal_id"))
    return ids


def _identities(rows: list[dict]) -> dict[str, tuple[str, list[str]]]:
    journals = _journal_ids()
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["entity_id"], []).append(row)
    assigned: dict[str, tuple[str, list[str]]] = {}
    used: set[str] = set()
    for entity_id, group in groups.items():
        keeper = None
        if entity_id not in journals:
            keeper = min(group, key=lambda row: (_CANONICAL_RANK.get(row["source_type"], 60), row["path"]))
        for row in group:
            if keeper is not None and row["path"] == keeper["path"] and entity_id not in used:
                assigned[row["path"]] = (entity_id, [])
                used.add(entity_id)
                continue
            external_id = row["source_id"]
            if external_id in used or external_id == entity_id:
                external_id = f"{row['source_id']}:{entity_id}"
            assigned[row["path"]] = (external_id, [entity_id])
            used.add(external_id)
    return assigned


def _record_v2_journals(db: Session, job: IngestJob, seen: set[str]) -> int:
    grouped: dict[str, list[dict]] = {}
    for row in _optional_csv("data_extensions/v2_journal_lines.csv"):
        grouped.setdefault(row["journal_id"], []).append(row)
    written = 0
    for journal_id, lines in grouped.items():
        if journal_id in seen:
            continue
        debits = [Decimal(line["debit_usd"] or "0") for line in lines]
        amount = sum((value for value in debits if value), Decimal("0"))
        when = utc(lines[0]["recorded_at"])
        references = []
        body = [f"Journal {journal_id}", f"Recorded at: {lines[0]['recorded_at']}", f"Accounting period: {lines[0]['accounting_period']}"]
        for line in lines:
            ref = (line.get("source_ref") or "").strip()
            if ref and ref not in references:
                references.append(ref)
            body.append(
                f"{line['line_id']} account {line['account']} debit ${line['debit_usd']} credit ${line['credit_usd']} {line.get('memo') or ''}".strip()
            )
        body.append("No formula is preserved for this entry.")
        _persist(
            db,
            job,
            filename=f"{journal_id}.txt",
            text="\n".join(body),
            external_id=journal_id,
            kind="journal",
            vendor=lines[0]["case_id"],
            amount=amount,
            document_date=when,
            available_from=when,
            case_id=lines[0]["case_id"],
            entity_ref=lines[0]["case_id"].lower(),
            references=references or [journal_id],
            scopes="finance,operations,controller",
            source_type="JOURNAL",
        )
        seen.add(journal_id)
        written += 1
    return written
