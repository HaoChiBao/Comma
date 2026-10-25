from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import workbook
from app.db import db_session

router = APIRouter(prefix="/v1")


class InspectIn(BaseModel):
    workbook_id: str
    sheet_name: str
    cell_address: str
    raw_value: float | int | str | bool | None = None
    display_text: str | None = None
    formula: str | None = None
    direct_precedents: list[str] = Field(default_factory=list, max_length=200)
    precedent_values: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    vendor_id: str | None = None
    period: str | None = None
    as_of: datetime
    actor_id: str | None = None


class ScanCellIn(BaseModel):
    address: str
    raw_value: float | int | str | bool | None = None
    formula: str | None = None


class ScanIn(BaseModel):
    workbook_id: str
    sheet_name: str
    range_address: str | None = None
    cells: list[ScanCellIn]
    as_of: datetime
    actor_id: str | None = None


@router.post("/traces/inspect")
def inspect_cell(body: InspectIn, db: Session = Depends(db_session)):
    return workbook.inspect(db, body.model_dump())


@router.post("/workbooks/scan")
def scan_range(body: ScanIn, db: Session = Depends(db_session)):
    return workbook.scan(db, body.model_dump())


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, db: Session = Depends(db_session)):
    return workbook.trace_run(db, trace_id).result_json


@router.get("/traces/{trace_id}/graph")
def get_trace_graph(trace_id: str, db: Session = Depends(db_session)):
    run = workbook.trace_run(db, trace_id)
    return {"trace_id": run.id, "as_of": run.as_of.isoformat(), **run.graph_json}


@router.get("/sources/{source_id}")
def get_source(source_id: str, trace_id: str, db: Session = Depends(db_session)):
    return workbook.source(db, trace_id, source_id)
