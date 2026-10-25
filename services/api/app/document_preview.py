"""Original bytes and a typed reading of one document.

Visibility is decided by the caller. Bytes come from the ingest row when that
row still holds them, otherwise from the company pack by the stored filename.
Nothing here opens an arbitrary path or skips the caller's check.
"""

import csv
import io
import re
import zipfile
from decimal import Decimal
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.decagon import PACK
from app.models import ArtifactVersion, IngestDocument, IngestRecord

_SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_HEADER = re.compile(r"^(from|date|subject)\s*:\s*(.*)$", re.I)
_ANY_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*\s*:")
_MAX_ROWS = 80
_MAX_COLS = 16
_PACK_INDEX: dict[str, str] | None = None


class Original(NamedTuple):
    data: bytes
    media_type: str
    filename: str


def preview_fields(db: Session, version: ArtifactVersion) -> dict:
    found = locate_original(db, version)
    data = found.data if found else None
    filename = found.filename if found else ""
    media = found.media_type if found else ""
    return describe(filename, media, data, version.content_text or "")


def locate_original(db: Session, version: ArtifactVersion) -> Original | None:
    record = db.scalar(select(IngestRecord).where(IngestRecord.version_id == version.id).limit(1))
    if record is None:
        return None
    document = db.get(IngestDocument, record.document_id)
    if document is None:
        return None
    filename = document.filename or ""
    data = bytes(document.payload or b"")
    if not data:
        data = _read_pack(filename) or b""
    if not data:
        return None
    return Original(data, _media_for(filename, document.media_type or "", data), filename)


def describe(filename: str, media_type: str, data: bytes | None, content_text: str) -> dict:
    name = (filename or "").lower()
    media = (media_type or "").split(";")[0].strip().lower()
    email = None
    table = None
    layout = "text"
    if data and data.startswith(b"%PDF"):
        layout = "pdf"
        media = "application/pdf"
    elif data and _is_image(name, media, data):
        layout = "image"
        media = _image_type(name, media, data)
    elif data and (media == "message/rfc822" or name.endswith(".eml")):
        email = _email_from_bytes(data)
        layout = "email" if email else "text"
        media = "message/rfc822"
    elif data and _is_table_file(name, media):
        rows, truncated = _table_rows(data, name, media)
        if rows:
            layout = "table"
            table = {"rows": rows, "truncated": truncated}
            if name.endswith(".csv") or "csv" in media:
                media = "text/csv"
        else:
            layout = "text"
    elif not data:
        email = _email_from_text(content_text)
        if email:
            layout = "email"
        media = media or "text/plain"
    elif name.endswith(".md") or media == "text/markdown":
        media = "text/markdown"
    elif not media:
        media = "text/plain"
    return {
        "filename": filename or None,
        "media_type": media or "application/octet-stream",
        "original": bool(data),
        "layout": layout,
        "email": email,
        "table": table,
    }


def content_disposition(filename: str) -> str:
    base = (filename or "document").replace("\\", "/").split("/")[-1]
    cleaned = "".join(char for char in base if char.isascii() and (char.isalnum() or char in "._- ")) or "document"
    return f'inline; filename="{cleaned[:180]}"'


def _read_pack(filename: str) -> bytes | None:
    base = (filename or "").replace("\\", "/").split("/")[-1].strip().lower()
    if not base or base in {".", ".."}:
        return None
    path = _pack_index().get(base)
    if not path or not PACK.exists():
        return None
    with zipfile.ZipFile(PACK) as archive:
        return archive.read(path)


def _pack_index() -> dict[str, str]:
    global _PACK_INDEX
    if _PACK_INDEX is not None:
        return _PACK_INDEX
    found: dict[str, str] = {}
    ambiguous: set[str] = set()
    if PACK.exists():
        with zipfile.ZipFile(PACK) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if base in found:
                    ambiguous.add(base)
                else:
                    found[base] = name
    for base in ambiguous:
        found.pop(base, None)
    _PACK_INDEX = found
    return found


def _media_for(filename: str, stored: str, data: bytes) -> str:
    name = filename.lower()
    media = stored.split(";")[0].strip().lower()
    if data.startswith(b"%PDF"):
        return "application/pdf"
    image = _image_type(name, media, data)
    if _is_image(name, media, data) and image:
        return image
    if media == "message/rfc822" or name.endswith(".eml"):
        return "message/rfc822"
    if name.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith(".csv") or "csv" in media:
        return "text/csv"
    if name.endswith(".md"):
        return "text/markdown"
    return media or "text/plain"


def _is_image(name: str, media: str, data: bytes) -> bool:
    if media.startswith("image/"):
        return True
    if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8") or data.startswith(b"GIF8"):
        return True
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return True
    return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _image_type(name: str, media: str, data: bytes) -> str:
    if data.startswith(b"\x89PNG") or name.endswith(".png"):
        return "image/png"
    if data.startswith(b"\xff\xd8") or name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if data.startswith(b"GIF8") or name.endswith(".gif"):
        return "image/gif"
    if (data.startswith(b"RIFF") and b"WEBP" in data[:16]) or name.endswith(".webp"):
        return "image/webp"
    return media if media.startswith("image/") else "image/png"


def _is_table_file(name: str, media: str) -> bool:
    return name.endswith((".csv", ".xlsx")) or "csv" in media or media.endswith("spreadsheetml.sheet")


def _table_rows(data: bytes, name: str, media: str) -> tuple[list[list[str]], bool]:
    if name.endswith(".xlsx") or media.endswith("spreadsheetml.sheet"):
        return _xlsx_rows(data)
    return _csv_rows(data)


def _csv_rows(data: bytes) -> tuple[list[list[str]], bool]:
    text = data.decode("utf-8-sig", errors="replace")
    rows: list[list[str]] = []
    truncated = False
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        if index >= _MAX_ROWS:
            truncated = True
            break
        if len(row) > _MAX_COLS:
            truncated = True
            row = row[:_MAX_COLS]
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() for cell in row])
    return rows, truncated


def _xlsx_rows(data: bytes) -> tuple[list[list[str]], bool]:
    import xml.etree.ElementTree as ET

    truncated = False
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [], False
    rows: list[list[str]] = []
    with archive:
        shared = _shared_strings(archive)
        sheets = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        for name in sheets:
            grid: dict[tuple[int, int], str] = {}
            root = ET.fromstring(archive.read(name))
            for cell in root.iter(f"{_SS}c"):
                ref = cell.attrib.get("r") or ""
                if not ref:
                    continue
                row_index, column_index = _cell_position(ref)
                if row_index >= _MAX_ROWS or column_index >= _MAX_COLS or row_index < 0 or column_index < 0:
                    truncated = True
                    continue
                value = _xlsx_value(cell, shared)
                if value:
                    grid[(row_index, column_index)] = value
            if not grid:
                continue
            height = max(row for row, _column in grid) + 1
            width = max(column for _row, column in grid) + 1
            for row_index in range(height):
                rows.append([grid.get((row_index, column), "") for column in range(width)])
    return _trim_table(rows), truncated


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    import xml.etree.ElementTree as ET

    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{_SS}t")) for item in root.findall(f"{_SS}si")]


def _cell_position(ref: str) -> tuple[int, int]:
    letters = []
    digits = []
    for char in ref:
        if char.isalpha():
            letters.append(char)
        elif char.isdigit():
            digits.append(char)
    column = 0
    for char in letters:
        column = column * 26 + (ord(char.upper()) - 64)
    row = int("".join(digits) or "1")
    return row - 1, column - 1


def _xlsx_value(cell, shared: list[str]) -> str:
    value = cell.find(f"{_SS}v")
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{_SS}t")).strip()
    if kind == "s" and value is not None and value.text and value.text.isdigit():
        index = int(value.text)
        return shared[index] if index < len(shared) else ""
    if value is None or not value.text:
        return ""
    if kind == "str":
        return value.text.strip()
    return _plain_number(value.text)


def _plain_number(value: str) -> str:
    try:
        number = Decimal(value)
    except Exception:
        return value.strip()
    if number == number.to_integral():
        return str(int(number))
    text = format(number, "f").rstrip("0").rstrip(".")
    return text


def _trim_table(rows: list[list[str]]) -> list[list[str]]:
    kept = [list(row) for row in rows if any(cell.strip() for cell in row)]
    if not kept:
        return []
    width = max(len(row) for row in kept)
    padded = [row + [""] * (width - len(row)) for row in kept]
    while padded and all(not row[-1].strip() for row in padded):
        for row in padded:
            row.pop()
        if padded and not padded[0]:
            break
    return padded


def _email_from_bytes(payload: bytes) -> dict | None:
    import email

    message = email.message_from_bytes(payload, policy=policy.default)
    fields: dict[str, str] = {}
    for key, name in (("from", "From"), ("date", "Date"), ("subject", "Subject")):
        value = _header(message, name)
        if value:
            fields[key] = value
    body = _email_body(message)
    if body:
        fields["body"] = body
    return fields or None


def _header(message: Message, name: str) -> str:
    value = message.get(name)
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def _email_body(message: Message) -> str:
    if message.get_content_type() == "text/plain" and not message.is_multipart():
        try:
            return _clean(message.get_content())
        except Exception:
            return ""
    plain: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment":
                continue
            try:
                content = part.get_content()
            except Exception:
                continue
            if content:
                plain.append(_clean(content))
        if plain:
            return "\n\n".join(item for item in plain if item)
        for part in message.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _strip_html(part.get_content() or "")
                except Exception:
                    continue
        return ""
    if message.get_content_type() == "text/html":
        try:
            return _strip_html(message.get_content() or "")
        except Exception:
            return ""
    try:
        return _clean(message.get_content())
    except Exception:
        return ""


def _email_from_text(text: str) -> dict | None:
    """Read From, Date, and Subject only when the text actually starts with mail headers."""
    lines = (text or "").splitlines()
    if not lines or _ANY_HEADER.match(lines[0].strip()) is None:
        return None
    headers: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            break
        match = _HEADER.match(line)
        if match:
            headers[match.group(1).lower()] = match.group(2).strip()
            index += 1
            continue
        if _ANY_HEADER.match(line):
            index += 1
            continue
        break
    if not any(headers.get(key) for key in ("from", "date", "subject")):
        return None
    fields = {key: headers[key] for key in ("from", "date", "subject") if headers.get(key)}
    body = "\n".join(lines[index:]).strip()
    if body:
        fields["body"] = body
    return fields or None


def _clean(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _strip_html(value: str) -> str:
    import html

    text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
