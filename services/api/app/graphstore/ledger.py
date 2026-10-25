"""Parse structured ledger CSVs into fact dicts.

Rows stay facts on an existing artifact version. They are not graph nodes.
``oracle/ground_truth.json`` is never opened.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ArtifactVersion, Fact, SourceArtifact

ORACLE_GROUND_TRUTH = "oracle/ground_truth.json"

_TRANSACTION_PREFIXES = ("PO-", "INV-", "ACR-", "JE-", "BANK-", "AUD-", "MSG-", "SVC-", "CTR-", "POL-")

# Column map (basename -> fact_type):
# general_ledger_lines.csv: source_ref (else journal_id), debit_usd/credit_usd, currency, line_id -> gl_amount
# invoice_headers.csv: invoice_id, amount_usd, currency, invoice_id -> invoice_amount
# invoice_lines.csv: invoice_id, line_total, line_id -> invoice_line_amount
# purchase_order_headers.csv: po_id, amount_usd, currency, po_id -> po_amount
# purchase_order_lines.csv: po_id, total_usd, po_line_id -> po_line_amount
# bank_transactions.csv: linked_invoice_id (else bank_tx_id), amount_usd, bank_tx_id -> bank_amount
# accrual_events.csv: accrual_id, amount_usd, record_id -> accrual_amount
# finance_audit_events.csv: resource_id, event_id, action -> audit_event (no amount)
# master_data.csv: record_id only when it is already a transaction id; vendor/account rows are skipped


class LedgerFact(TypedDict):
    transaction_external_id: str
    fact_type: str
    amount: Decimal | None
    currency: str
    source_locator: str
    value_text: str


def load_ledger_facts(zip_path: str | Path) -> list[LedgerFact]:
    facts: list[LedgerFact] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or _is_oracle(name):
                continue
            filename = name.rsplit("/", 1)[-1]
            parser = _PARSERS.get(filename)
            if parser is None:
                continue
            payload = archive.read(info).decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(payload)):
                fact = parser(filename, row)
                if fact is not None:
                    facts.append(fact)
    facts.sort(key=lambda item: (item["source_locator"], item["fact_type"], item["transaction_external_id"]))
    return facts


def persist_ledger_facts(db: Session, organization_id: str, facts: list[dict]) -> int:
    """Insert Fact rows for external ids that already have an artifact version.

    Missing versions are skipped. Source artifacts are not created. A second
    call with the same facts writes nothing new.
    """
    written = 0
    version_ids = _version_ids(db, organization_id)
    existing = {
        (row.artifact_version_id, row.fact_type, row.source_locator)
        for row in db.execute(
            select(Fact.artifact_version_id, Fact.fact_type, Fact.source_locator)
            .join(ArtifactVersion, Fact.artifact_version_id == ArtifactVersion.id)
            .join(SourceArtifact, ArtifactVersion.artifact_id == SourceArtifact.id)
            .where(SourceArtifact.organization_id == organization_id)
        ).all()
    }
    for fact in facts:
        external_id = str(fact.get("transaction_external_id") or "").strip()
        fact_type = str(fact.get("fact_type") or "").strip()[:64]
        locator = str(fact.get("source_locator") or "").strip()[:200]
        if not external_id or not fact_type or not locator:
            continue
        version_id = version_ids.get(external_id[:64])
        if version_id is None:
            continue
        key = (version_id, fact_type, locator)
        if key in existing:
            continue
        existing.add(key)
        amount = fact.get("amount")
        if amount is not None:
            amount = Decimal(amount).quantize(Decimal("0.01"))
        db.add(
            Fact(
                id=_fact_id(version_id, fact_type, locator),
                artifact_version_id=version_id,
                fact_type=fact_type,
                entity_ref=external_id[:80],
                amount_decimal=amount,
                currency=str(fact.get("currency") or "USD")[:8],
                value_text=str(fact.get("value_text") or "")[:240],
                source_locator=locator,
                extraction_status="extracted",
            )
        )
        written += 1
    if written:
        db.flush()
    return written


def _version_ids(db: Session, organization_id: str) -> dict[str, str]:
    artifacts = db.scalars(
        select(SourceArtifact).where(SourceArtifact.organization_id == organization_id).order_by(SourceArtifact.id)
    ).all()
    chosen: dict[str, str] = {}
    for artifact in artifacts:
        chosen.setdefault(artifact.external_id, artifact.id)
    if not chosen:
        return {}
    best: dict[str, ArtifactVersion] = {}
    for version in db.scalars(select(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(list(chosen.values())))).all():
        current = best.get(version.artifact_id)
        if current is None or (version.version, version.id) > (current.version, current.id):
            best[version.artifact_id] = version
    return {external_id: best[artifact_id].id for external_id, artifact_id in chosen.items() if artifact_id in best}


def _fact_id(version_id: str, fact_type: str, source_locator: str) -> str:
    digest = hashlib.sha256(f"{version_id}|{fact_type}|{source_locator}".encode()).hexdigest()
    return f"lf-{digest}"[:80]


def _is_oracle(name: str) -> bool:
    return name.replace("\\", "/").lower().endswith(ORACLE_GROUND_TRUTH)


def _transaction_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper().startswith(_TRANSACTION_PREFIXES):
        return text
    return None


def _money(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if text in {"", "-", "."}:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _currency(row: dict) -> str:
    raw = str(row.get("currency") or "").strip().upper()
    if raw:
        return raw[:8]
    return "USD"


def _locator(filename: str, row_id: str | None) -> str:
    token = str(row_id or "").strip() or "row"
    return f"{filename}:{token}"[:200]


def _text(amount: Decimal | None, currency: str, note: str = "") -> str:
    parts: list[str] = []
    cleaned = " ".join(note.split())
    if cleaned:
        parts.append(cleaned[:40])
    if amount is not None:
        parts.append(f"{amount} {currency}".strip())
    return " ".join(parts)[:120]


def _fact(
    transaction_external_id: str,
    fact_type: str,
    amount: Decimal | None,
    currency: str,
    source_locator: str,
    value_text: str,
) -> LedgerFact:
    return {
        "transaction_external_id": transaction_external_id,
        "fact_type": fact_type,
        "amount": amount,
        "currency": currency,
        "source_locator": source_locator,
        "value_text": value_text,
    }


def _gl_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("source_ref")) or _transaction_id(row.get("journal_id"))
    if external_id is None:
        return None
    debit = _money(row.get("debit_usd"))
    credit = _money(row.get("credit_usd"))
    if debit is not None and debit != 0 and (credit is None or credit == 0):
        amount, side = debit, "debit"
    elif credit is not None and credit != 0 and (debit is None or debit == 0):
        amount, side = credit, "credit"
    elif debit is None and credit is None:
        amount, side = None, ""
    else:
        amount = (debit or Decimal("0.00")) - (credit or Decimal("0.00"))
        side = "net"
    currency = _currency(row)
    return _fact(external_id, "gl_amount", amount, currency, _locator(filename, row.get("line_id")), _text(amount, currency, side))


def _invoice_header_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("invoice_id"))
    if external_id is None:
        return None
    amount = _money(row.get("amount_usd"))
    currency = _currency(row)
    return _fact(external_id, "invoice_amount", amount, currency, _locator(filename, row.get("invoice_id")), _text(amount, currency))


def _invoice_line_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("invoice_id"))
    if external_id is None:
        return None
    amount = _money(row.get("line_total"))
    currency = _currency(row)
    return _fact(external_id, "invoice_line_amount", amount, currency, _locator(filename, row.get("line_id")), _text(amount, currency))


def _po_header_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("po_id"))
    if external_id is None:
        return None
    amount = _money(row.get("amount_usd"))
    currency = _currency(row)
    return _fact(external_id, "po_amount", amount, currency, _locator(filename, row.get("po_id")), _text(amount, currency))


def _po_line_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("po_id"))
    if external_id is None:
        return None
    amount = _money(row.get("total_usd"))
    currency = _currency(row)
    return _fact(external_id, "po_line_amount", amount, currency, _locator(filename, row.get("po_line_id")), _text(amount, currency))


def _bank_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("linked_invoice_id")) or _transaction_id(row.get("bank_tx_id"))
    if external_id is None:
        return None
    amount = _money(row.get("amount_usd"))
    currency = _currency(row)
    note = str(row.get("direction") or "").strip()
    return _fact(external_id, "bank_amount", amount, currency, _locator(filename, row.get("bank_tx_id")), _text(amount, currency, note))


def _accrual_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("accrual_id")) or _transaction_id(row.get("record_id"))
    if external_id is None:
        return None
    amount = _money(row.get("amount_usd"))
    currency = _currency(row)
    return _fact(external_id, "accrual_amount", amount, currency, _locator(filename, row.get("record_id")), _text(amount, currency))


def _audit_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("resource_id"))
    if external_id is None:
        return None
    action = str(row.get("action") or "").strip()
    return _fact(external_id, "audit_event", None, _currency(row), _locator(filename, row.get("event_id")), _text(None, "", action))


def _master_fact(filename: str, row: dict) -> LedgerFact | None:
    external_id = _transaction_id(row.get("record_id"))
    if external_id is None:
        return None
    label = str(row.get("display_name") or row.get("record_type") or "").strip()
    return _fact(external_id, "master_record", None, "USD", _locator(filename, row.get("record_id")), _text(None, "", label))


_PARSERS: dict[str, Callable[[str, dict], LedgerFact | None]] = {
    "general_ledger_lines.csv": _gl_fact,
    "invoice_headers.csv": _invoice_header_fact,
    "invoice_lines.csv": _invoice_line_fact,
    "purchase_order_headers.csv": _po_header_fact,
    "purchase_order_lines.csv": _po_line_fact,
    "bank_transactions.csv": _bank_fact,
    "accrual_events.csv": _accrual_fact,
    "finance_audit_events.csv": _audit_fact,
    "master_data.csv": _master_fact,
}
