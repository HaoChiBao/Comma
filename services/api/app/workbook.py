"""Cell-level tracing for the Excel add-in, on the company document graph.

Formula lineage comes only from the add-in (Office.js precedents). A registered cell carries its
row's ids (accrual, purchase order, invoice). Evidence is found from what ingest stored: the
documents with those ids, documents that mention them, and documents filed under the same vendor.
Every one passes through an organization-wide snapshot, so a source that was not available to
this actor at `as_of` contributes its id and the exclusion reason, never its content or amount.
"""

import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import append_event
from app.graphstore.entities import normalize_alias
from app.models import ArtifactVersion, Claim, Fact, IngestDocument, IngestRecord, Mention, Snapshot, SourceArtifact, TraceRun, WorkbookCell
from app.snapshots import AccessError, create_org_snapshot, denial_code, guarded_version, load_version
from app.temporal import aware, money

DEFAULT_ACTOR = "actor-decagon-finance"
MAX_SCAN_CELLS = 200
CELL_PATTERN = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})$")
AGREEING = {"match", "within_tolerance"}
LEDGER_TYPES = ("accrual_amount", "po_amount", "invoice_amount")
VENDOR_KINDS = {"contract"}
ORIGIN_TYPES = ("derived_from", "source_candidate", "unexplained")
NODE_TYPE = {"purchase_order": "PurchaseOrder", "email": "Email", "invoice": "Invoice", "journal": "Journal", "policy": "Policy", "contract": "Contract"}
CONFLICT_TITLE = {
    "email": "Potential estimate discrepancy",
    "contract": "Amount differs from the contract",
    "purchase_order": "Amount differs from the purchase order",
    "invoice": "Amount differs from the invoice",
}


class WorkbookError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(code)


def parse_ref(ref: str, default_sheet: str) -> tuple[str, str]:
    """Accepts `B3`, `$B$3`, `Accruals!B3`, `'My Sheet'!B3`, or `[Book1]Accruals!B3`."""
    sheet = default_sheet
    address = ref.strip()
    if "!" in address:
        sheet, address = address.rsplit("!", 1)
        sheet = re.sub(r"^\[[^\]]*\]", "", sheet.strip().strip("'"))
    match = CELL_PATTERN.match(address.upper())
    if match is None or not sheet:
        raise WorkbookError(400, "INVALID_CELL", f"{ref!r} is not a single cell address")
    return sheet, f"{match.group(1)}{match.group(2)}"


def cell_key(sheet: str, address: str) -> str:
    return f"{sheet}!{address}"


def as_amount(value) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        return None


def tolerance(value: Decimal) -> Decimal:
    absolute = Decimal(os.environ.get("WORLDSTATE_TOLERANCE_ABS", "1.00"))
    relative = Decimal(os.environ.get("WORLDSTATE_TOLERANCE_PCT", "0.01"))
    return max(absolute, abs(value) * relative)


def compare(cell: WorkbookCell, value: Decimal | None, amount: Decimal | None, currency: str, period: str | None) -> str:
    """Compares only like with like: same currency, and same period when the source states one."""
    if value is None or amount is None:
        return "no_amount"
    if currency != cell.currency:
        return "not_comparable_currency"
    if period and period != cell.period:
        return "not_comparable_period"
    difference = abs(Decimal(amount) - value)
    if difference == 0:
        return "match"
    if difference <= tolerance(value):
        return "within_tolerance"
    return "differs"


def registered(db: Session, workbook_id: str, sheet: str, address: str) -> WorkbookCell | None:
    return db.scalar(
        select(WorkbookCell).where(
            WorkbookCell.workbook_id == workbook_id,
            WorkbookCell.sheet_name == sheet,
            WorkbookCell.address == address,
        )
    )


def workbook_organization(db: Session, workbook_id: str) -> str:
    """The organization that owns the workbook. The company workbook registers itself once ingest finishes."""
    organization_id = db.scalar(select(WorkbookCell.organization_id).where(WorkbookCell.workbook_id == workbook_id).limit(1))
    if organization_id is not None:
        return organization_id
    from app.ingest.decagon import ORG
    from app.ingest.decagon_workbook import WORKBOOK_ID, ensure_workbook, workbook_ready

    if workbook_id != WORKBOOK_ID:
        raise WorkbookError(404, "WORKBOOK_NOT_FOUND", f"Workbook {workbook_id!r} is not registered with WorldState")
    if not workbook_ready(db, ORG):
        raise WorkbookError(503, "INDEX_NOT_READY", "WorldState is still reading the company documents. Try again in a moment.")
    try:
        ensure_workbook(db)
    except IntegrityError:
        # Another request registered the cells first.
        db.rollback()
    return ORG


def _versions_for(db: Session, organization_id: str, external_ids: set[str]) -> dict[str, ArtifactVersion]:
    if not external_ids:
        return {}
    rows = db.execute(
        select(SourceArtifact.external_id, ArtifactVersion)
        .join(ArtifactVersion, ArtifactVersion.artifact_id == SourceArtifact.id)
        .where(SourceArtifact.organization_id == organization_id, SourceArtifact.external_id.in_(external_ids))
    ).all()
    latest: dict[str, ArtifactVersion] = {}
    for external_id, version in rows:
        if external_id not in latest or version.version > latest[external_id].version:
            latest[external_id] = version
    return latest


def find_links(db: Session, cell: WorkbookCell) -> list[dict]:
    """Which stored documents bear on this cell, and why. Contents are not read here."""
    refs = (cell.value_json or {}).get("refs") or {}
    column = (cell.value_json or {}).get("column")
    links: dict[str, dict] = {}

    def add(version_id: str, external_id: str, relationship: str, status: str, basis: str) -> None:
        links.setdefault(version_id, {"version_id": version_id, "external_id": external_id, "relationship": relationship, "link_status": status, "basis": basis})

    if column == "po":
        for external_id, version in _versions_for(db, cell.organization_id, {cell.reference}).items():
            add(version.id, external_id, "SUPPORTED_BY", "verified", f"PO ref column names {external_id}")
        return list(links.values())

    named = {
        refs.get("accrual"): ("RECORDED_AS", "The posted accrual entry"),
        refs.get("po"): ("SUPPORTED_BY", "PO ref column"),
        refs.get("invoice"): ("POSSIBLY_RELATED_TO", "The case's invoice"),
    }
    named.pop(None, None)
    named.pop("", None)
    for external_id, version in _versions_for(db, cell.organization_id, set(named)).items():
        relationship, basis = named[external_id]
        add(version.id, external_id, relationship, "verified", f"{basis}: {external_id}")
    # Documents that name the PO or the invoice.
    mentioned = {item for item in (refs.get("po"), refs.get("invoice")) if item}
    order = [item for item in (refs.get("po"), refs.get("invoice")) if item]
    found = db.scalars(select(Mention).where(Mention.mentioned_external_id.in_(mentioned))).all()
    # Cite the purchase order first when a document names both.
    for mention in sorted(found, key=lambda item: (order.index(item.mentioned_external_id), item.version_id)):
        version = load_version(db, mention.version_id)
        if version is None or version.artifact.organization_id != cell.organization_id:
            continue
        add(version.id, version.artifact.external_id, "POSSIBLY_RELATED_TO", "candidate", f"{version.artifact.external_id} names {mention.mentioned_external_id}")
    # Contracts never name the PO; they are filed under the same vendor. Other kinds would pull in unrelated months.
    alias = normalize_alias(cell.vendor_name)
    if alias:
        for record in db.scalars(select(IngestRecord).where(IngestRecord.kind.in_(VENDOR_KINDS)).order_by(IngestRecord.external_id)).all():
            if not record.version_id or normalize_alias(record.vendor) != alias:
                continue
            version = load_version(db, record.version_id)
            if version is None or version.artifact.organization_id != cell.organization_id:
                continue
            add(version.id, record.external_id, "POSSIBLY_RELATED_TO", "candidate", f"Filed under vendor {cell.vendor_name}")
    return list(links.values())


def cell_evidence(db: Session, snapshot: Snapshot, cell: WorkbookCell, value: Decimal | None) -> list[dict]:
    key = cell_key(cell.sheet_name, cell.address)
    evidence = []
    for link in find_links(db, cell):
        try:
            version = guarded_version(db, snapshot, link["version_id"])
        except AccessError:
            evidence.append(
                {
                    "cell": key,
                    "source_id": link["version_id"],
                    "status": "excluded",
                    "reason": denial_code(db, snapshot, link["version_id"]),
                    "relationship": link["relationship"],
                    "link_status": link["link_status"],
                }
            )
            continue
        amount_fact = next((fact for fact in version.facts if fact.fact_type == "amount"), None)
        amount = amount_fact.amount_decimal if amount_fact else None
        currency = amount_fact.currency if amount_fact else cell.currency
        period = (version.metadata_json or {}).get("period")
        comparison = compare(cell, value, amount, currency, period)
        difference = money(abs(Decimal(amount) - value)) if comparison in {"differs", "within_tolerance"} else None
        relationship = link["relationship"]
        if comparison == "differs" and relationship != "RECORDED_AS":
            relationship = "CONFLICTS_WITH"
        evidence.append(
            {
                "cell": key,
                "source_id": version.id,
                "status": "available",
                "external_id": version.artifact.external_id,
                "kind": version.artifact.kind,
                "title": version.artifact.title,
                "amount": money(amount),
                "currency": currency,
                "period": period,
                "document_date": version.document_date.isoformat(),
                "available_at": version.available_from.isoformat() if version.available_from else None,
                "locator": _document_name(db, version.id) or version.artifact.external_id,
                "ledger_locator": _ledger_locator(version),
                "snippet": version.content_text[:280],
                "relationship": relationship,
                "link_status": link["link_status"],
                "basis": link["basis"],
                "comparison": comparison,
                "difference": difference,
            }
        )
    return evidence


def origin_for(db: Session, snapshot: Snapshot, cell: WorkbookCell | None) -> dict | None:
    """The stored origin claim for an accrual, kept only when all of its evidence is in the snapshot."""
    if cell is None or (cell.value_json or {}).get("column") != "accrual":
        return None
    allowed = set(snapshot.allowed_manifest_json)
    moment = aware(snapshot.as_of)
    claims = db.scalars(
        select(Claim).where(
            Claim.organization_id == cell.organization_id,
            Claim.to_node_id == cell.reference,
            Claim.claim_type.in_(ORIGIN_TYPES),
        )
    ).all()
    visible = [
        claim
        for claim in claims
        if claim.knowable_from is not None
        and aware(claim.knowable_from) <= moment
        and all(version_id in allowed for version_id in claim.evidence_version_ids or [])
    ]
    if not visible:
        return None
    claim = sorted(visible, key=lambda item: (ORIGIN_TYPES.index(item.claim_type), item.id))[0]
    evidence_ids = []
    for version_id in claim.evidence_version_ids or []:
        version = load_version(db, version_id)
        if version is not None and version.artifact.external_id != cell.reference:
            evidence_ids.append(version.artifact.external_id)
    return {
        "claim_type": claim.claim_type,
        "status": claim.status,
        "formulas": list(claim.formulas or []),
        "explanation": claim.explanation,
        "evidence": evidence_ids,
        "knowable_from": aware(claim.knowable_from).isoformat(),
    }


def assess(cell: WorkbookCell | None, value: Decimal | None, evidence: list[dict], origin: dict | None, as_of: datetime) -> dict:
    """Deterministic status. Review means a person should look; it never means the cell is wrong."""
    if cell is None:
        return _result("unknown", "Not tracked by WorldState", "This cell is not in the WorldState registry for this workbook.", None, ["No source links exist for this cell."])
    if value is None:
        return _result("unknown", "No numeric value", "WorldState compares numeric amounts only.", None, [])
    key = cell_key(cell.sheet_name, cell.address)
    own = [item for item in evidence if item["cell"] == key]
    available = [item for item in own if item["status"] == "available"]
    excluded = [item for item in own if item["status"] == "excluded"]
    unknowns = []
    if excluded:
        unknowns.append(f"{len(excluded)} linked source(s) were not available to this role as of {as_of.date().isoformat()} and are withheld.")
    candidates = sorted({item["external_id"] for item in available if item["link_status"] == "candidate"})
    if candidates:
        unknowns.append(f"No proof that the preparer read {', '.join(candidates)}. These links are candidates.")
    for item in available:
        if item["comparison"] == "not_comparable_currency":
            unknowns.append(f"{item['external_id']} is in {item['currency']}; not compared with a {cell.currency} cell.")
        elif item["comparison"] == "not_comparable_period":
            unknowns.append(f"{item['external_id']} covers {item['period']}; not compared with {cell.period}.")
    refs = (cell.value_json or {}).get("refs") or {}
    if (cell.value_json or {}).get("column") == "accrual" and not refs.get("po"):
        unknowns.append("No purchase order is on file for this accrual.")
    if origin is not None and origin["claim_type"] == "unexplained":
        unknowns.append(origin["explanation"])
    if not own:
        unknowns.append("No preserved source explains this amount.")
        return _result("unknown", "Unknown source", f"No source on file for {_usd(value)}. Request support before relying on it.", None, unknowns)
    recorded = next((item for item in available if item["relationship"] == "RECORDED_AS" and item["comparison"] == "differs"), None)
    if recorded is not None:
        summary = f"The posted entry {recorded['external_id']} is {_usd(Decimal(recorded['amount']))}; this cell is {_usd(value)}. Review the workbook against the ledger."
        return _result("review", "Cell differs from the posted accrual", summary, {"amount": recorded["difference"], "currency": cell.currency}, unknowns)
    conflicts = [item for item in available if item["relationship"] == "CONFLICTS_WITH"]
    if conflicts:
        item = max(conflicts, key=lambda entry: (Decimal(entry["difference"]), entry["external_id"]))
        when = aware(datetime.fromisoformat(item["available_at"])).strftime("%b %d") if item["available_at"] else "an unknown date"
        others = sorted({entry["external_id"] for entry in conflicts if entry is not item})
        also = f" {', '.join(others)} also differ{'s' if len(others) == 1 else ''}." if others else ""
        summary = (
            f"{item['external_id']} ({item['kind'].replace('_', ' ')}, received {when}) shows {_usd(Decimal(item['amount']))}; "
            f"this cell is {_usd(value)} (difference {_usd(Decimal(item['difference']))}).{also} This may be reasonable; review."
        )
        title = CONFLICT_TITLE.get(item["kind"], "Amount differs from a source")
        return _result("review", title, summary, {"amount": item["difference"], "currency": cell.currency}, unknowns)
    supported = [
        item for item in available if item["link_status"] == "verified" and item["relationship"] == "SUPPORTED_BY" and item["comparison"] in AGREEING
    ]
    if supported:
        names = ", ".join(sorted({item["external_id"] for item in supported}))
        return _result("supported", f"Supported by {names}", f"{_usd(value)} agrees with {names}.", None, unknowns)
    agreeing = sorted({item["external_id"] for item in available if item["comparison"] in AGREEING and item["relationship"] != "RECORDED_AS"})
    if agreeing:
        summary = f"{_usd(value)} agrees with {', '.join(agreeing)}, but no verified source such as a purchase order confirms it."
        return _result("unknown", "Only candidate evidence", summary, None, unknowns)
    if origin is not None and origin["claim_type"] == "unexplained":
        return _result("unknown", "Origin unexplained", f"No source available as of {as_of.date().isoformat()} accounts for {_usd(value)}.", None, unknowns)
    return _result("unknown", "Insufficient evidence", f"No linked source available as of {as_of.date().isoformat()} confirms {_usd(value)}.", None, unknowns)


def depends_on(precedent_results: dict[str, dict]) -> dict:
    """A formula cell that is not registered takes the most serious status of its registered inputs."""
    flagged = [key for key, result in precedent_results.items() if result["status"] == "review"]
    unknown = [key for key, result in precedent_results.items() if result["status"] == "unknown"]
    if flagged:
        names = ", ".join(key.split("!")[1] for key in flagged)
        return _result("review", f"Depends on {names}", f"This formula uses {names}, which needs review: {precedent_results[flagged[0]]['summary']}", None, [])
    if unknown:
        names = ", ".join(key.split("!")[1] for key in unknown)
        return _result("unknown", f"Depends on {names}", f"This formula uses {names}, whose source is unknown.", None, [])
    names = ", ".join(key.split("!")[1] for key in precedent_results)
    return _result("supported", "Inputs are supported", f"Every registered input ({names}) agrees with its sources.", None, [])


def inspect(db: Session, body: dict) -> dict:
    workbook_id = body["workbook_id"]
    sheet, address = parse_ref(body["cell_address"], body["sheet_name"])
    key = cell_key(sheet, address)
    as_of = _aware(body["as_of"])
    organization_id = workbook_organization(db, workbook_id)
    snapshot = _snapshot(db, body.get("actor_id") or DEFAULT_ACTOR, as_of, organization_id)
    value = as_amount(body.get("raw_value"))
    cell = registered(db, workbook_id, sheet, address)
    precedent_values = {cell_key(*parse_ref(ref, sheet)): amount for ref, amount in (body.get("precedent_values") or {}).items()}
    precedents = []
    for ref in body.get("direct_precedents") or []:
        precedent = cell_key(*parse_ref(ref, sheet))
        if precedent not in precedents:
            precedents.append(precedent)
    evidence = cell_evidence(db, snapshot, cell, value) if cell else []
    origin = origin_for(db, snapshot, cell)
    precedent_cells: dict[str, Decimal | None] = {}
    precedent_results: dict[str, dict] = {}
    case_id = cell.case_id if cell else None
    for precedent in precedents:
        precedent_sheet, precedent_address = precedent.split("!", 1)
        linked = registered(db, workbook_id, precedent_sheet, precedent_address)
        live = as_amount(precedent_values.get(precedent))
        if live is None and linked is not None:
            live = as_amount((linked.value_json or {}).get("amount"))
        precedent_cells[precedent] = live
        if linked is not None:
            found = cell_evidence(db, snapshot, linked, live)
            evidence.extend(found)
            precedent_results[precedent] = assess(linked, live, found, origin_for(db, snapshot, linked), as_of)
            case_id = case_id or linked.case_id
            origin = origin or origin_for(db, snapshot, linked)
    if cell is None and precedent_results:
        assessment = depends_on(precedent_results)
    else:
        assessment = assess(cell, value, evidence, origin, as_of)
    trace_id = f"trc_{uuid4().hex[:16]}"
    result = {
        "trace_id": trace_id,
        "snapshot_id": snapshot.id,
        "manifest_hash": snapshot.manifest_hash,
        "as_of": as_of.isoformat(),
        "actor_id": snapshot.actor_id,
        "case_id": case_id,
        "cell": {
            "key": key,
            "sheet_name": sheet,
            "address": address,
            "raw_value": body.get("raw_value"),
            "display_text": body.get("display_text"),
            "formula": body.get("formula") or "",
            "registered": cell is not None,
            "vendor_name": cell.vendor_name if cell else None,
            "period": cell.period if cell else None,
            "reference": cell.reference if cell else None,
        },
        **assessment,
        "origin": origin,
        "lineage": [{"from": precedent, "to": key, "kind": "FORMULA_DEPENDS_ON", "verified": True} for precedent in precedents],
        "evidence": evidence,
    }
    graph = _graph(db, snapshot, key, value, assessment["status"], precedent_cells, evidence, origin)
    db.add(
        TraceRun(
            id=trace_id,
            organization_id=snapshot.organization_id,
            workbook_id=workbook_id,
            cell_key=key,
            actor_id=snapshot.actor_id,
            snapshot_id=snapshot.id,
            as_of=as_of,
            manifest_hash=snapshot.manifest_hash,
            result_json=result,
            graph_json=graph,
            created_at=datetime.now(timezone.utc),
        )
    )
    append_event(
        db,
        snapshot.organization_id,
        "cell_traced",
        trace_id,
        {"workbook_id": workbook_id, "cell": key, "status": assessment["status"], "snapshot_id": snapshot.id},
        snapshot.actor_id,
    )
    db.commit()
    return result


def scan(db: Session, body: dict) -> dict:
    cells = body.get("cells") or []
    if len(cells) > MAX_SCAN_CELLS:
        raise WorkbookError(400, "RANGE_TOO_LARGE", f"Scan at most {MAX_SCAN_CELLS} cells at a time")
    workbook_id = body["workbook_id"]
    as_of = _aware(body["as_of"])
    organization_id = workbook_organization(db, workbook_id)
    snapshot = _snapshot(db, body.get("actor_id") or DEFAULT_ACTOR, as_of, organization_id)
    findings = []
    for item in cells:
        sheet, address = parse_ref(item["address"], body["sheet_name"])
        cell = registered(db, workbook_id, sheet, address)
        if cell is None:
            continue
        value = as_amount(item.get("raw_value"))
        evidence = cell_evidence(db, snapshot, cell, value)
        assessment = assess(cell, value, evidence, origin_for(db, snapshot, cell), as_of)
        findings.append({"cell": cell_key(sheet, address), "sheet_name": sheet, "address": address, "case_id": cell.case_id, **assessment})
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1
    append_event(db, snapshot.organization_id, "range_scanned", snapshot.id, {"workbook_id": workbook_id, "counts": counts}, snapshot.actor_id)
    db.commit()
    return {"snapshot_id": snapshot.id, "as_of": as_of.isoformat(), "scanned": len(cells), "counts": counts, "findings": findings}


def trace_run(db: Session, trace_id: str) -> TraceRun:
    run = db.get(TraceRun, trace_id)
    if run is None:
        raise WorkbookError(404, "TRACE_NOT_FOUND", f"Trace {trace_id!r} does not exist")
    return run


def source(db: Session, trace_id: str, source_id: str) -> dict:
    """Source content is served only through a trace's snapshot, so a later document stays sealed."""
    run = trace_run(db, trace_id)
    version = guarded_version(db, db.get(Snapshot, run.snapshot_id), source_id)
    return {
        "source_id": version.id,
        "external_id": version.artifact.external_id,
        "kind": version.artifact.kind,
        "title": version.artifact.title,
        "content_text": version.content_text,
        "content_hash": version.content_hash,
        "document_date": version.document_date.isoformat(),
        "available_at": version.available_from.isoformat() if version.available_from else None,
        "availability_evidence": version.availability_evidence,
        "facts": [
            {"fact_id": fact.id, "amount": money(fact.amount_decimal), "currency": fact.currency, "locator": fact.source_locator, "fact_type": fact.fact_type}
            for fact in sorted(version.facts, key=lambda item: (item.fact_type != "amount", item.source_locator))
            if fact.amount_decimal is not None
        ],
        "trace_id": run.id,
        "as_of": run.as_of.isoformat(),
    }


def _snapshot(db: Session, actor_id: str, as_of: datetime, organization_id: str) -> Snapshot:
    snapshot = create_org_snapshot(db, actor_id=actor_id, as_of=as_of)
    if snapshot.organization_id != organization_id:
        raise AccessError("OUTSIDE_ORGANIZATION")
    return snapshot


def _document_name(db: Session, version_id: str) -> str:
    record = db.scalar(select(IngestRecord).where(IngestRecord.version_id == version_id).limit(1))
    if record is None:
        return ""
    document = db.get(IngestDocument, record.document_id)
    return document.filename if document else ""


def _ledger_locator(version: ArtifactVersion) -> str | None:
    """The ledger row that records this document's own amount, e.g. `purchase_order_headers.csv:PO-C01-1123`."""
    own = version.artifact.external_id
    for fact_type in LEDGER_TYPES:
        for fact in sorted(version.facts, key=lambda item: item.source_locator):
            if fact.fact_type == fact_type and fact.entity_ref == own and fact.source_locator.endswith(own):
                return fact.source_locator
    return None


def _graph(db: Session, snapshot: Snapshot, key: str, value: Decimal | None, status: str, precedents: dict, evidence: list[dict], origin: dict | None) -> dict:
    nodes = {key: {"id": key, "type": "SpreadsheetCell", "label": key.split("!")[1], "title": key, "amount": money(value), "status": status, "selected": True}}
    edges = []
    for precedent, amount in precedents.items():
        nodes[precedent] = {"id": precedent, "type": "SpreadsheetCell", "label": precedent.split("!")[1], "title": precedent, "amount": money(amount)}
        edges.append({"id": f"{precedent}->{key}", "source": precedent, "target": key, "type": "FORMULA_DEPENDS_ON", "verified": True, "inferred": False})
    by_external: dict[str, str] = {}
    for item in evidence:
        if item["status"] == "excluded":
            node_id = f"blocked:{item['source_id']}"
            nodes[node_id] = {"id": node_id, "type": "Blocked", "label": "Withheld", "title": "Not available at this date or to this role", "amount": None, "reason": item["reason"], "source_id": None}
            edges.append({"id": f"{item['cell']}->{node_id}", "source": item["cell"], "target": node_id, "type": "EXCLUDED", "verified": False, "inferred": item["link_status"] != "verified"})
            continue
        node_id = f"source:{item['source_id']}"
        by_external[item["external_id"]] = node_id
        nodes.setdefault(
            node_id,
            {
                "id": node_id,
                "type": NODE_TYPE.get(item["kind"], "Document"),
                "label": item["external_id"],
                "title": item["title"],
                "amount": item["amount"],
                "currency": item["currency"],
                "available_at": item["available_at"],
                "source_id": item["source_id"],
            },
        )
        edges.append(
            {
                "id": f"{item['cell']}->{node_id}",
                "source": item["cell"],
                "target": node_id,
                "type": item["relationship"],
                "verified": item["link_status"] == "verified",
                "inferred": item["link_status"] != "verified",
                "locator": item["ledger_locator"] or item["locator"],
                "comparison": item["comparison"],
            }
        )
    # Stored mentions between documents already on the graph.
    visible = {item["source_id"] for item in evidence if item["status"] == "available"}
    for mention in db.scalars(select(Mention).where(Mention.version_id.in_(visible), Mention.mentioned_external_id.in_(list(by_external)))).all():
        source_node = f"source:{mention.version_id}"
        target_node = by_external.get(mention.mentioned_external_id)
        if target_node and target_node != source_node:
            edges.append({"id": f"{source_node}~{target_node}", "source": source_node, "target": target_node, "type": "MENTIONS", "verified": True, "inferred": False})
    if origin is not None and origin["claim_type"] != "unexplained":
        recorded = next((by_external[item["external_id"]] for item in evidence if item.get("relationship") == "RECORDED_AS" and item["external_id"] in by_external), None)
        edge_type = "DERIVED_FROM" if origin["claim_type"] == "derived_from" else "SOURCE_CANDIDATE"
        for external_id in origin["evidence"]:
            if recorded and external_id in by_external:
                edges.append({"id": f"{by_external[external_id]}=>{recorded}", "source": by_external[external_id], "target": recorded, "type": edge_type, "verified": False, "inferred": True})
    return {"nodes": list(nodes.values()), "edges": edges}


def _result(status: str, title: str, summary: str, difference: dict | None, unknowns: list[str]) -> dict:
    return {"status": status, "title": title, "summary": summary, "difference": difference, "unknowns": unknowns}


def _usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
