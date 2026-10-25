import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal

from app.temporal import utc

ID_RE = re.compile(r"\b((?:PO|INV|JE|EM|POL|CT|MSG|CTR|AMD|SVC|ACR)-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b", re.I)
MONEY_RE = re.compile(
    r"(?:(?:USD\s*)?\$\s*(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2}))"
    r"|(?:\b(?:authorized\s+amount|invoice\s+total|amount|total|accrual|price|estimate|balance)\b(?:[^0-9$]|\n){0,48}(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2}))"
    r"|(?:\bUSD\b(?:[^0-9$]|\n){0,16}(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2}))",
    re.I,
)
ISO_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
RECEIVED_RE = re.compile(
    r"\b(?:received(?:\s+by\s+[A-Za-z]+)?|available(?:\s+from)?|accessible|first accessible)\b(?:[^0-9]|\n){0,48}(20\d{2}-\d{2}-\d{2})",
    re.I,
)
VENDOR_RE = re.compile(r"\b(?:vendor|supplier|payee|from)\b[:\s]+([A-Za-z][\w&.,' -]{2,80})", re.I)
UNIT_RE = re.compile(r"\bunit(?:\s+price)?\b[^0-9$\n]{0,16}\$?\s*(\d+(?:\.\d{2})?)", re.I)
QTY_RE = re.compile(r"\b(?:quantity|qty)\b[^0-9\n]{0,12}(\d+(?:\.\d+)?)", re.I)


def extract_text(filename: str, media_type: str, payload: bytes) -> str:
    name = filename.lower()
    if name.endswith(".eml") or media_type == "message/rfc822":
        return _email_text(payload)
    if name.endswith(".pdf") or media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if name.endswith(".xlsx"):
        return _xlsx_text(payload)
    return payload.decode("utf-8", errors="replace").strip()


def _xlsx_text(payload: bytes) -> str:
    import xml.etree.ElementTree as ET
    import zipfile

    space = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(payload)) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{space}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{space}t")))
        sheets = sorted(name for name in book.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        parts = []
        for name in sheets:
            root = ET.fromstring(book.read(name))
            lines = []
            for cell in root.iter(f"{space}c"):
                ref = cell.attrib.get("r", "")
                formula = cell.find(f"{space}f")
                value = cell.find(f"{space}v")
                text = ""
                if cell.attrib.get("t") == "s" and value is not None and value.text and value.text.isdigit():
                    index = int(value.text)
                    text = shared[index] if index < len(shared) else ""
                elif cell.attrib.get("t") == "inlineStr":
                    text = "".join(node.text or "" for node in cell.iter(f"{space}t"))
                elif value is not None and value.text:
                    text = _xlsx_number(value.text)
                formula_text = (formula.text or "").strip() if formula is not None else ""
                if formula_text:
                    shown = formula_text if formula_text.startswith("=") else f"={formula_text}"
                    lines.append(f"{ref} {shown} => {text}".strip())
                elif text:
                    lines.append(f"{ref} {text}".strip())
            if lines:
                parts.append("\n".join(lines))
    return "\n\n".join(parts).strip()


def _xlsx_number(value: str) -> str:
    try:
        number = Decimal(value)
    except Exception:
        return value
    if number == number.to_integral() and 2000 <= number <= 2100:
        return str(int(number))
    if Decimal("43000") <= number <= Decimal("52000"):
        from datetime import datetime, timedelta

        moment = datetime(1899, 12, 30) + timedelta(days=float(number))
        return moment.strftime("%Y-%m-%d")
    return f"USD {number.quantize(Decimal('0.01'))}"


def _email_text(payload: bytes) -> str:
    import email
    from email import policy

    message = email.message_from_bytes(payload, policy=policy.default)
    body = message.get_content() if not message.is_multipart() else ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                body += part.get_content() or ""
    headers = []
    for key in ("From", "To", "Subject", "Date", "Message-ID"):
        if message[key]:
            headers.append(f"{key}: {message[key]}")
    return "\n".join(headers + ["", body]).strip()


def parse_document(text: str, fallback_available: datetime) -> list[dict]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("The document has no readable text.")
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return [_from_mapping(item, fallback_available, text) for item in parsed]
    if _looks_like_csv(stripped):
        return [_from_mapping(row, fallback_available, "\n".join(f"{key}: {value}" for key, value in row.items())) for row in _csv_rows(stripped)]
    if _kind(stripped) == "email":
        record = _from_text(stripped, fallback_available)
        records = [record]
        _fill_missing_amounts(records, stripped)
        return records
    chunks = _chunks(stripped)
    records = []
    carried = ""
    for chunk in chunks:
        if not (ID_RE.search(chunk) or MONEY_RE.search(chunk) or _kind(chunk) != "memo"):
            carried += "\n" + chunk
            continue
        records.append(_from_text((carried + "\n" + chunk).strip(), fallback_available))
        carried = ""
    if carried.strip():
        if records:
            records[-1]["text"] = (records[-1]["text"] + "\n" + carried).strip()
        else:
            records.append(_from_text(stripped, fallback_available))
    if not records:
        records.append(_from_text(stripped, fallback_available))
    _fill_missing_amounts(records, stripped)
    return records


def _chunks(text: str) -> list[str]:
    parts = re.split(r"\n\s*(?:---+|\f)\s*\n|\n{2,}", text)
    return [part.strip() for part in parts if part.strip()]


def _looks_like_csv(text: str) -> bool:
    first = text.splitlines()[0].lower()
    return "," in first and any(word in first for word in ("amount", "total", "vendor", "invoice", "po"))


def _csv_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _from_mapping(item: dict, fallback_available: datetime, text: str) -> dict:
    blob = text if text else json.dumps(item)
    record = _from_text(blob, fallback_available)
    for key in ("id", "external_id", "document_id", "number"):
        if item.get(key):
            record["external_id"] = _normalize_id(str(item[key]))
            break
    if item.get("kind") or item.get("type"):
        record["kind"] = _canonical_kind(str(item.get("kind") or item.get("type")))
    if item.get("vendor"):
        record["vendor"] = str(item["vendor"]).strip()
    if item.get("amount") not in (None, ""):
        record["amount"] = _decimal(str(item["amount"]))
    return record


def _from_text(text: str, fallback_available: datetime) -> dict:
    identifiers = [_normalize_id(match) for match in ID_RE.findall(text)]
    kind = _kind(text)
    external_id = _primary_id(identifiers, kind) or _slug(text[:48]) or "untitled"
    dates = [utc(match + "T00:00:00+00:00") for match in ISO_RE.findall(text)]
    received = RECEIVED_RE.search(text)
    available = utc(received.group(1) + "T00:00:00+00:00") if received else fallback_available
    document_date = dates[0] if dates else None
    vendor_match = VENDOR_RE.search(text)
    vendor = vendor_match.group(1).strip(" .,-") if vendor_match else ""
    amount = _first_money(text)
    unit = _decimal(UNIT_RE.search(text).group(1)) if UNIT_RE.search(text) else None
    quantity = _decimal(QTY_RE.search(text).group(1)) if QTY_RE.search(text) else None
    formula = ""
    if unit is not None and quantity is not None and amount is not None and unit * quantity == amount:
        formula = "estimated total = unit_price * estimated_quantity"
    return {
        "external_id": external_id,
        "kind": kind,
        "vendor": vendor,
        "references": identifiers,
        "amount": amount,
        "document_date": document_date,
        "available_from": available,
        "text": text.strip(),
        "formula": formula,
        "unit_price": unit,
        "quantity": quantity,
    }


def _fill_missing_amounts(records: list[dict], text: str) -> None:
    found = {_money_key(record["amount"]) for record in records if record["amount"] is not None}
    for match in MONEY_RE.finditer(text):
        amount = _match_amount(match)
        key = _money_key(amount)
        if key in found:
            continue
        records.append(
            {
                "external_id": f"amount-{key.replace('.', '')}",
                "kind": "memo",
                "vendor": records[0]["vendor"] if records else "",
                "references": [],
                "amount": amount,
                "document_date": records[0]["document_date"] if records else None,
                "available_from": records[0]["available_from"] if records else utc("2025-01-01T00:00:00+00:00"),
                "text": match.group(0),
                "formula": "",
                "unit_price": None,
                "quantity": None,
            }
        )
        found.add(key)


def _first_money(text: str) -> Decimal | None:
    match = MONEY_RE.search(text)
    if not match:
        return None
    return _match_amount(match)


def _match_amount(match: re.Match) -> Decimal:
    groups = [group for group in match.groups() if group]
    whole = groups[0].replace(",", "")
    fraction = groups[1] if len(groups) > 1 else "00"
    return Decimal(f"{whole}.{fraction}")


def _money_key(amount: Decimal | None) -> str:
    return "" if amount is None else f"{amount:.2f}"


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", "")).quantize(Decimal("0.01")) if "." in value else Decimal(value)


def _kind(text: str) -> str:
    head = "\n".join(text.splitlines()[:16]).lower()
    if re.search(r"(?m)^(from|subject):", head):
        return "email"
    for line in head.splitlines():
        if line.startswith("purchase order") or line.startswith("po "):
            return "purchase_order"
        if line.startswith("invoice") or line.startswith("vendor invoice"):
            return "invoice"
        if line.startswith("journal") or line.startswith("accrual"):
            return "journal"
        if line.startswith("policy"):
            return "policy"
        if "amendment" in line or line.startswith("contract") or line.startswith("master services"):
            return "contract"
    if re.search(r"\bpurchase order\b", text, re.I):
        return "purchase_order"
    if re.search(r"\binvoice\b", text, re.I):
        return "invoice"
    if re.search(r"\b(journal|accrual)\b", text, re.I):
        return "journal"
    return "memo"


def _canonical_kind(value: str) -> str:
    cleaned = value.lower().replace(" ", "_")
    aliases = {"po": "purchase_order", "purchaseorder": "purchase_order", "accrual": "journal", "je": "journal"}
    return aliases.get(cleaned, cleaned if cleaned in {"invoice", "purchase_order", "journal", "email", "policy", "memo", "contract"} else "memo")


def _primary_id(identifiers: list[str], kind: str) -> str:
    prefixes = {
        "invoice": ("INV-",),
        "purchase_order": ("PO-",),
        "journal": ("JE-", "ACR-"),
        "email": ("MSG-", "EM-"),
        "policy": ("POL-",),
        "contract": ("CTR-", "AMD-"),
    }
    wanted = prefixes.get(kind, ())
    for prefix in wanted:
        for identifier in identifiers:
            if identifier.startswith(prefix):
                return identifier
    return identifiers[0] if identifiers else ""


def _normalize_id(value: str) -> str:
    compact = re.sub(r"\s+", "", value.strip())
    match = re.match(r"([A-Za-z]+)([\s-]?)(.*)", compact)
    if not match:
        return compact.upper()
    rest = match.group(3)
    if match.group(2) == "":
        rest = "-" + rest
    return f"{match.group(1).upper()}-{rest.lstrip('-')}" if rest else match.group(1).upper()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40]
