"""Cell-level tracing for the Excel add-in.

Formula lineage comes only from the add-in (Office.js precedents). Source evidence comes only from the
snapshot allowlist, so a source that was not available at `as_of` contributes its id and the exclusion
reason, never its content or amount.
"""

import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import append_event
from app.models import CellSourceLink, Fact, Snapshot, TraceRun, WorkbookCell
from app.snapshots import AccessError, create_snapshot, denial_code, guarded_version
from app.temporal import money

MAX_SCAN_CELLS = 200
CELL_PATTERN = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})$")
CONFLICTING = {"differs", "vendor_mismatch"}
AGREEING = {"match", "within_tolerance"}
NODE_TYPE = {"purchase_order": "PurchaseOrder", "email": "Email", "invoice": "Invoice", "journal": "Document", "policy": "Policy"}


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


def compare(cell: WorkbookCell, value: Decimal | None, fact: Fact) -> str:
    """Compares only like with like: same currency, same period, same vendor."""
    if value is None or fact.amount_decimal is None:
        return "no_amount"
    if fact.currency != cell.currency:
        return "not_comparable_currency"
    metadata = fact.version.metadata_json or {}
    if metadata.get("period") and metadata["period"] != cell.period:
        return "not_comparable_period"
    if metadata.get("entity_ref") and metadata["entity_ref"] != cell.vendor_ref:
        return "vendor_mismatch"
    difference = abs(Decimal(fact.amount_decimal) - value)
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


def workbook_case(db: Session, workbook_id: str) -> str:
    case_id = db.scalar(select(WorkbookCell.case_id).where(WorkbookCell.workbook_id == workbook_id).limit(1))
    if case_id is None:
        raise WorkbookError(404, "WORKBOOK_NOT_FOUND", f"Workbook {workbook_id!r} is not registered with WorldState")
    return case_id


def cell_evidence(db: Session, snapshot: Snapshot, cell: WorkbookCell, value: Decimal | None) -> list[dict]:
    links = db.scalars(select(CellSourceLink).where(CellSourceLink.cell_id == cell.id).order_by(CellSourceLink.id)).all()
    key = cell_key(cell.sheet_name, cell.address)
    evidence = []
    for link in links:
        fact = db.get(Fact, link.fact_id)
        if fact is None:
            continue
        try:
            version = guarded_version(db, snapshot, fact.artifact_version_id)
        except AccessError:
            evidence.append(
                {
                    "cell": key,
                    "source_id": fact.artifact_version_id,
                    "status": "excluded",
                    "reason": denial_code(db, snapshot, fact.artifact_version_id),
                    "relationship": link.relationship_type,
                    "link_status": link.status,
                }
            )
            continue
        comparison = compare(cell, value, fact)
        difference = None
        if comparison in {"differs", "within_tolerance"}:
            difference = money(abs(Decimal(fact.amount_decimal) - value))
        evidence.append(
            {
                "cell": key,
                "source_id": version.id,
                "status": "available",
                "external_id": version.artifact.external_id,
                "kind": version.artifact.kind,
                "title": version.artifact.title,
                "amount": money(fact.amount_decimal),
                "currency": fact.currency,
                "period": (version.metadata_json or {}).get("period"),
                "document_date": version.document_date.isoformat(),
                "available_at": version.available_from.isoformat() if version.available_from else None,
                "locator": fact.source_locator,
                "snippet": version.content_text[:280],
                "relationship": "CONFLICTS_WITH" if comparison in CONFLICTING else link.relationship_type,
                "link_status": link.status,
                "basis": link.basis,
                "comparison": comparison,
                "difference": difference,
            }
        )
    return evidence


def assess(db: Session, cell: WorkbookCell | None, value: Decimal | None, evidence: list[dict], as_of: datetime) -> dict:
    """Deterministic status. Review means a person should look; it never means the cell is wrong."""
    if cell is None:
        return _result("unknown", "Not tracked by WorldState", "This cell is not in the WorldState registry for this workbook.", None, ["No source links exist for this cell."])
    if value is None:
        return _result("unknown", "No numeric value", "WorldState compares numeric amounts only.", None, [])
    available = [item for item in evidence if item["status"] == "available"]
    excluded = [item for item in evidence if item["status"] == "excluded"]
    unknowns = []
    if excluded:
        unknowns.append(f"{len(excluded)} linked source(s) were not available as of {as_of.date().isoformat()} and are withheld.")
    candidates = sorted({item["external_id"] for item in available if item["link_status"] == "candidate"})
    if candidates:
        unknowns.append(f"No proof that the preparer read {', '.join(candidates)}. These links are candidates.")
    for item in available:
        if item["comparison"] == "not_comparable_currency":
            unknowns.append(f"{item['external_id']} is in {item['currency']}; not compared with a {cell.currency} cell.")
        elif item["comparison"] == "not_comparable_period":
            unknowns.append(f"{item['external_id']} covers {item['period']}; not compared with {cell.period}.")
    if not evidence:
        unknowns.append("No preserved source explains this amount.")
        return _result("unknown", "Unknown source", f"No source on file for {_usd(value)}. Request support before relying on it.", None, unknowns)
    mismatched = [item for item in available if item["comparison"] == "vendor_mismatch"]
    if mismatched:
        item = mismatched[0]
        others = db.scalars(
            select(WorkbookCell.address).where(
                WorkbookCell.workbook_id == cell.workbook_id,
                WorkbookCell.reference == cell.reference,
                WorkbookCell.vendor_ref != cell.vendor_ref,
                WorkbookCell.address.like("D%"),
            )
        ).all()
        also = f" {cell.reference} is also cited on {', '.join(others)}." if others else ""
        summary = f"{cell.vendor_name} cites {item['external_id']}, which is a purchase order for a different vendor.{also} Review the reference."
        return _result("review", "Reference points to a different vendor", summary, None, unknowns)
    conflicts = [item for item in available if item["comparison"] == "differs"]
    if conflicts:
        item = max(conflicts, key=lambda entry: Decimal(entry["difference"]))
        when = datetime.fromisoformat(item["available_at"]).strftime("%b %d") if item["available_at"] else "an unknown date"
        title = "Potential estimate discrepancy" if item["kind"] == "email" else "Amount differs from source"
        summary = (
            f"{item['external_id']} ({item['kind'].replace('_', ' ')}, received {when}) shows {_usd(Decimal(item['amount']))}; "
            f"this cell is {_usd(value)} (difference {_usd(Decimal(item['difference']))}). This may be reasonable; review."
        )
        return _result("review", title, summary, {"amount": item["difference"], "currency": cell.currency}, unknowns)
    supported = [item for item in available if item["link_status"] == "verified" and item["comparison"] in AGREEING and item["cell"] == cell_key(cell.sheet_name, cell.address)]
    if supported:
        names = ", ".join(sorted({item["external_id"] for item in supported}))
        return _result("supported", f"Supported by {names}", f"{_usd(value)} agrees with {names}.", None, unknowns)
    if not available:
        return _result("unknown", "Insufficient evidence", f"No linked source was available as of {as_of.date().isoformat()}.", None, unknowns)
    return _result("unknown", "Insufficient evidence", "Only candidate or non-comparable evidence is available.", None, unknowns)


def inspect(db: Session, body: dict) -> dict:
    workbook_id = body["workbook_id"]
    sheet, address = parse_ref(body["cell_address"], body["sheet_name"])
    key = cell_key(sheet, address)
    as_of = _aware(body["as_of"])
    snapshot = create_snapshot(db, case_id=workbook_case(db, workbook_id), actor_id=body["actor_id"], as_of=as_of, mode="audit")
    value = as_amount(body.get("raw_value"))
    cell = registered(db, workbook_id, sheet, address)
    precedent_values = {cell_key(*parse_ref(ref, sheet)): amount for ref, amount in (body.get("precedent_values") or {}).items()}
    precedents = []
    for ref in body.get("direct_precedents") or []:
        precedent = cell_key(*parse_ref(ref, sheet))
        if precedent not in precedents:
            precedents.append(precedent)
    evidence = cell_evidence(db, snapshot, cell, value) if cell else []
    precedent_cells = {}
    for precedent in precedents:
        precedent_sheet, precedent_address = precedent.split("!", 1)
        linked = registered(db, workbook_id, precedent_sheet, precedent_address)
        live = as_amount(precedent_values.get(precedent))
        if live is None and linked is not None:
            live = as_amount(linked.value_json.get("amount"))
        precedent_cells[precedent] = live
        if linked is not None:
            evidence.extend(cell_evidence(db, snapshot, linked, live))
    assessment = assess(db, cell, value, evidence, as_of)
    trace_id = f"trc_{uuid4().hex[:16]}"
    result = {
        "trace_id": trace_id,
        "snapshot_id": snapshot.id,
        "manifest_hash": snapshot.manifest_hash,
        "as_of": as_of.isoformat(),
        "actor_id": snapshot.actor_id,
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
        "lineage": [{"from": precedent, "to": key, "kind": "FORMULA_DEPENDS_ON", "verified": True} for precedent in precedents],
        "evidence": evidence,
    }
    graph = _graph(key, value, assessment["status"], precedent_cells, evidence)
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
    snapshot = create_snapshot(db, case_id=workbook_case(db, workbook_id), actor_id=body["actor_id"], as_of=as_of, mode="audit")
    findings = []
    for item in cells:
        sheet, address = parse_ref(item["address"], body["sheet_name"])
        cell = registered(db, workbook_id, sheet, address)
        if cell is None:
            continue
        value = as_amount(item.get("raw_value"))
        evidence = cell_evidence(db, snapshot, cell, value)
        assessment = assess(db, cell, value, evidence, as_of)
        findings.append({"cell": cell_key(sheet, address), "sheet_name": sheet, "address": address, **assessment})
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
            {"fact_id": fact.id, "amount": money(fact.amount_decimal), "currency": fact.currency, "locator": fact.source_locator}
            for fact in version.facts
            if fact.amount_decimal is not None
        ],
        "trace_id": run.id,
        "as_of": run.as_of.isoformat(),
    }


def _graph(key: str, value: Decimal | None, status: str, precedents: dict, evidence: list[dict]) -> dict:
    nodes = {key: {"id": key, "type": "SpreadsheetCell", "label": key.split("!")[1], "title": key, "amount": money(value), "status": status, "selected": True}}
    edges = []
    for precedent, amount in precedents.items():
        nodes[precedent] = {"id": precedent, "type": "SpreadsheetCell", "label": precedent.split("!")[1], "title": precedent, "amount": money(amount)}
        edges.append({"id": f"{precedent}->{key}", "source": precedent, "target": key, "type": "FORMULA_DEPENDS_ON", "verified": True, "inferred": False})
    for item in evidence:
        if item["status"] == "excluded":
            node_id = f"blocked:{item['source_id']}"
            nodes[node_id] = {"id": node_id, "type": "Blocked", "label": "Not yet available", "title": "Withheld at this date", "amount": None, "reason": item["reason"], "source_id": None}
            edges.append({"id": f"{item['cell']}->{node_id}", "source": item["cell"], "target": node_id, "type": "EXCLUDED", "verified": False, "inferred": item["link_status"] != "verified"})
            continue
        node_id = f"source:{item['source_id']}"
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
                "locator": item["locator"],
                "comparison": item["comparison"],
            }
        )
    return {"nodes": list(nodes.values()), "edges": edges}


def _result(status: str, title: str, summary: str, difference: dict | None, unknowns: list[str]) -> dict:
    return {"status": status, "title": title, "summary": summary, "difference": difference, "unknowns": unknowns}


def _usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
