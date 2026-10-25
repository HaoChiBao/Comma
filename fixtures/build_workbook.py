"""Builds fixtures/Decagon_Accruals.xlsx from the Decagon ledger, the same rows the API registers.

    pip install openpyxl
    PYTHONPATH=services/api python fixtures/build_workbook.py
"""

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.packaging.custom import StringProperty
from openpyxl.styles import Font, PatternFill

from app.ingest.decagon import PACK
from app.ingest.decagon_workbook import HEADER, SHEET, WORKBOOK_ID, workbook_rows

MONEY = '"$"#,##0.00'


def build(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET
    sheet.append(HEADER)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
    for row in workbook_rows(PACK):
        n = row["row"]
        sheet.cell(row=n, column=1, value=row["case_id"])
        sheet.cell(row=n, column=2, value=row["vendor_name"])
        sheet.cell(row=n, column=3, value=row["accrual_id"])
        sheet.cell(row=n, column=4, value=row["po_id"] or None)
        if row["po_amount"] is not None:
            sheet.cell(row=n, column=5, value=float(row["po_amount"]))
            sheet.cell(row=n, column=7, value=f"=F{n}-E{n}")
        sheet.cell(row=n, column=6, value=float(row["accrual_amount"]))
        sheet.cell(row=n, column=8, value=row["period"])
        for column in (5, 6, 7):
            sheet.cell(row=n, column=column).number_format = MONEY
    for column, width in zip("ABCDEFGH", (7, 30, 17, 14, 12, 12, 11, 9)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    # The add-in reads this to know which registered workbook it is looking at.
    workbook.custom_doc_props.append(StringProperty(name="WorldStateWorkbookId", value=WORKBOOK_ID))
    workbook.save(path)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("Decagon_Accruals.xlsx")
    print(build(target))
