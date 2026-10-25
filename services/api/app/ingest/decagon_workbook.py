"""The September accruals workbook, built from the Decagon ledger.

One row per case. The invoice amount is left out on purpose: a September close workbook cannot
contain an invoice that accounting received in October or December. The invoice id is still kept
server-side so a trace can find (and, at an earlier date, withhold) that invoice.
"""

import csv
import io
import zipfile
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Case, IngestJob, WorkbookCell

WORKBOOK_ID = "decagon-accruals-2025-09"
SHEET = "Accruals"
HEADER = ["Case", "Vendor", "Accrual ID", "PO ref", "PO amount", "Accrual", "Variance", "Period"]
FIRST_ROW = 2


def workbook_rows(zip_path: str | Path) -> list[dict]:
    from app.ingest.decagon import ROOT

    with zipfile.ZipFile(zip_path) as archive:
        def table(name: str) -> list[dict]:
            return list(csv.DictReader(io.StringIO(archive.read(ROOT + name).decode("utf-8-sig"))))

        registry = table("metadata/case_registry.csv")
        accruals = {row["accrual_id"]: row for row in table("structured/accrual_events.csv") if row["event_type"] == "ACCRUAL"}
        orders = {row["po_id"]: row for row in table("structured/purchase_order_headers.csv")}
        vendors = {row["record_id"]: row["display_name"] for row in table("structured/master_data.csv") if row["record_type"] == "vendor"}
    rows = []
    for index, case in enumerate(sorted(registry, key=lambda row: row["case_id"])):
        accrual = accruals[case["accrual_id"]]
        order = orders.get(case["po_id"]) if case["po_id"] else None
        rows.append(
            {
                "row": FIRST_ROW + index,
                "case_id": case["case_id"],
                "vendor_id": case["vendor_id"],
                "vendor_name": vendors.get(case["vendor_id"], case["vendor_id"]),
                "accrual_id": case["accrual_id"],
                "po_id": case["po_id"] or "",
                "invoice_id": case["invoice_id"] or "",
                "po_amount": Decimal(order["amount_usd"]) if order else None,
                "accrual_amount": Decimal(accrual["amount_usd"]),
                "currency": (order or {}).get("currency") or "USD",
                "period": accrual["effective_date"][:7],
            }
        )
    return rows


def cells_for(row: dict) -> list[dict]:
    """Registry entries for one row. E is the PO amount, F the recorded accrual, G = F - E when a PO exists."""
    n = row["row"]
    refs = {"accrual": row["accrual_id"], "po": row["po_id"], "invoice": row["invoice_id"]}
    cells = [
        {"address": f"F{n}", "column": "accrual", "reference": row["accrual_id"], "amount": row["accrual_amount"], "formula": ""},
    ]
    if row["po_amount"] is not None:
        cells.append({"address": f"E{n}", "column": "po", "reference": row["po_id"], "amount": row["po_amount"], "formula": ""})
    return [{**cell, "refs": refs} for cell in cells]


def workbook_ready(db: Session, organization_id: str) -> bool:
    job = db.scalars(select(IngestJob).where(IngestJob.organization_id == organization_id).order_by(IngestJob.created_at.desc())).first()
    return job is not None and job.status == "completed"


def ensure_workbook(db: Session) -> int:
    """Register the workbook's cells once the company ingest has finished. Safe to call repeatedly."""
    from app.ingest.decagon import ORG, PACK

    if not PACK.exists() or not workbook_ready(db, ORG):
        return 0
    existing = {cell.address for cell in db.scalars(select(WorkbookCell).where(WorkbookCell.workbook_id == WORKBOOK_ID)).all()}
    written = 0
    for row in workbook_rows(PACK):
        if db.get(Case, row["case_id"]) is None:
            continue
        for cell in cells_for(row):
            if cell["address"] in existing:
                continue
            db.add(
                WorkbookCell(
                    id=f"{WORKBOOK_ID}:{SHEET}!{cell['address']}",
                    organization_id=ORG,
                    workbook_id=WORKBOOK_ID,
                    case_id=row["case_id"],
                    sheet_name=SHEET,
                    address=cell["address"],
                    value_json={"amount": f"{cell['amount']:.2f}", "column": cell["column"], "refs": cell["refs"]},
                    formula=cell["formula"],
                    period=row["period"],
                    currency=row["currency"],
                    vendor_name=row["vendor_name"],
                    vendor_ref=row["vendor_id"],
                    reference=cell["reference"],
                )
            )
            written += 1
    if written:
        db.commit()
    return written
