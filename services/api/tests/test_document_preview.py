import io
import json
import os
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ["INGEST_INLINE"] = "1"

SEPT = {
    "organization_id": "org-acme",
    "actor_id": "actor-maya",
    "as_of": "2025-09-30T23:59:00+00:00",
    "mode": "audit",
}
DEC = {**SEPT, "as_of": "2025-12-15T12:00:00+00:00"}
POISON = b"%PDF-1.4\nFinal invoice INV-381 Amount due 158.00 Received in December\n"


def client():
    from app.db import session
    from app.main import create_app
    from app.seed import seed

    app = create_app("sqlite:///:memory:")
    with session() as db:
        seed(db)
    return TestClient(app)


def test_visible_file_and_hidden_september_invoice():
    api = client()
    from app.db import session

    with session() as db:
        _store(db, "preview-mail", "email", "MSG-0001", "A long mail record. " * 30, "MSG-0001_C01.eml", "message/rfc822", b"")
        _store(db, "preview-po", "purchase_order", "PO-C01-1123", "Purchase order kept in full. " * 30, "PO-C01-1123.pdf", "application/pdf", b"")
        _store(db, "preview-csv", "memo", "GRID-1", "grid", "grid.csv", "text/csv", b"Vendor,Amount\nNorthwind,12.50\n")
        _store(db, "preview-sheet", "memo", "GRID-2", "sheet", "grid.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _xlsx())
        _store(db, "preview-image", "memo", "IMG-1", "image", "mark.png", "image/png", _png())
        _attach(db, "INV-381:v1", "hidden-invoice.pdf", "application/pdf", POISON)

    mail = api.get("/api/v1/company-graph/documents/preview-mail", params=SEPT)
    assert mail.status_code == 200, mail.text
    message = mail.json()
    assert message["original"] is True
    assert message["layout"] == "email"
    assert message["email"]["from"] == "accounting@vend-001.example"
    assert "Sep 2025" in message["email"]["date"]
    assert "revised estimate" in message["email"]["subject"].lower()
    assert "160.00" in message["email"]["body"]
    assert "=E2" not in message["email"]["body"]
    assert len(message["text"]) > 360

    pdf = api.get("/api/v1/company-graph/documents/preview-po", params=SEPT)
    assert pdf.status_code == 200, pdf.text
    assert pdf.json()["layout"] == "pdf"
    assert pdf.json()["original"] is True
    assert "content_base64" not in pdf.json()
    assert len(pdf.json()["text"]) > 360
    pdf_file = api.get("/api/v1/company-graph/documents/preview-po/file", params=SEPT)
    assert pdf_file.status_code == 200, pdf_file.text
    assert pdf_file.content.startswith(b"%PDF")
    assert pdf_file.headers["content-type"].startswith("application/pdf")

    table = api.get("/api/v1/company-graph/documents/preview-csv", params=SEPT)
    assert table.status_code == 200, table.text
    assert table.json()["layout"] == "table"
    assert table.json()["table"]["rows"] == [["Vendor", "Amount"], ["Northwind", "12.50"]]
    assert "B3" not in json.dumps(table.json())
    assert "=" not in json.dumps(table.json()["table"])

    sheet = api.get("/api/v1/company-graph/documents/preview-sheet", params=SEPT)
    assert sheet.status_code == 200, sheet.text
    rendered = json.dumps(sheet.json()["table"])
    assert sheet.json()["table"]["rows"] == [["Vendor", "12.5"], ["Northwind", "40"]]
    assert "B3" not in rendered
    assert "B3+C3" not in rendered
    assert "=" not in rendered

    image = api.get("/api/v1/company-graph/documents/preview-image/file", params=SEPT)
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG")
    assert image.headers["content-type"].startswith("image/png")

    prose = api.get("/api/v1/company-graph/documents/PO-101:v1", params=SEPT)
    assert prose.status_code == 200, prose.text
    assert prose.json()["original"] is False
    assert prose.json()["layout"] == "text"
    assert prose.json()["email"] is None
    assert "Purchase order PO-101" in prose.json()["text"]
    missing = api.get("/api/v1/company-graph/documents/PO-101:v1/file", params=SEPT)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Original file is not in this snapshot."
    assert b"%PDF" not in missing.content

    letter = api.get("/api/v1/company-graph/documents/EM-207:v1", params=SEPT)
    assert letter.json()["layout"] == "text"
    assert letter.json()["email"] is None
    assert letter.json()["original"] is False

    hidden = api.get("/api/v1/company-graph/documents/INV-381:v1", params=SEPT)
    assert hidden.status_code == 403
    _assert_hidden(hidden)
    hidden_file = api.get("/api/v1/company-graph/documents/INV-381:v1/file", params=SEPT)
    assert hidden_file.status_code == 403
    _assert_hidden(hidden_file)
    assert b"%PDF" not in hidden_file.content

    opened = api.get("/api/v1/company-graph/documents/INV-381:v1", params=DEC)
    assert opened.status_code == 200, opened.text
    assert opened.json()["original"] is True
    assert opened.json()["layout"] == "pdf"
    assert "158.00" in opened.json()["text"]
    opened_file = api.get("/api/v1/company-graph/documents/INV-381:v1/file", params=DEC)
    assert opened_file.status_code == 200
    assert opened_file.content == POISON


def _assert_hidden(response):
    body = response.json()
    assert body["message"] == "This source is not available to this actor at this date."
    assert "title" not in body
    assert "amount" not in body
    assert "text" not in body
    visible = response.text.replace(body.get("request_id") or "", "")
    for secret in ("158", "158.00", "INV-381", "Amount due", "December", "%PDF"):
        assert secret not in visible


def _store(db, version_id, kind, external_id, text, filename, media, payload):
    from app.models import AccessGrant, ArtifactVersion, SourceArtifact
    from app.temporal import sha256_text, utc

    when = utc("2025-09-12T00:00:00+00:00")
    now = datetime.now(timezone.utc)
    db.add(
        SourceArtifact(
            id=f"art-{version_id}",
            organization_id="org-acme",
            kind=kind,
            source_system="test",
            external_id=external_id,
            title=external_id,
        )
    )
    db.add(
        ArtifactVersion(
            id=version_id,
            artifact_id=f"art-{version_id}",
            version=1,
            content_text=text,
            content_hash=sha256_text(text),
            document_date=when,
            business_valid_from=None,
            business_valid_to=None,
            available_from=when,
            available_to=None,
            ingested_at=now,
            availability_status="verified",
            availability_evidence="test",
            metadata_json={"case_ids": ["CASE-001"], "entity_ref": "vendor-cloud", "formula": ""},
        )
    )
    db.add(
        AccessGrant(
            id=f"grant-{version_id}-accountant",
            artifact_version_id=version_id,
            role="accountant",
            grant_from=when,
            grant_to=None,
            permission="read",
        )
    )
    _attach(db, version_id, filename, media, payload, text=text, when=when)


def _attach(db, version_id, filename, media, payload, text="", when=None):
    from app.models import IngestDocument, IngestJob, IngestRecord
    from app.temporal import utc

    moment = when or utc("2025-09-12T00:00:00+00:00")
    now = datetime.now(timezone.utc)
    job_id = uuid4().hex[:16]
    document_id = uuid4().hex[:16]
    db.add(
        IngestJob(
            id=job_id,
            organization_id="org-acme",
            status="completed",
            available_from=moment,
            error="",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        IngestDocument(
            id=document_id,
            job_id=job_id,
            filename=filename,
            media_type=media,
            payload=payload,
            text=text,
            content_hash="",
        )
    )
    db.add(
        IngestRecord(
            id=uuid4().hex[:16],
            job_id=job_id,
            document_id=document_id,
            external_id=version_id[:80],
            kind="memo",
            vendor="",
            entity_ref="vendor-cloud",
            references_json=[],
            amount=None,
            currency="USD",
            document_date=moment,
            available_from=moment,
            text=text,
            formula="",
            version_id=version_id,
            episode_id="",
        )
    )
    db.commit()


def _xlsx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""",
        )
        book.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        book.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        book.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
<si><t>Vendor</t></si><si><t>Northwind</t></si></sst>""",
        )
        book.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>12.5</v></c></row>
<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><f>B3+C3</f><v>40</v></c><c r="C2"><f>B3+C3</f></c></row>
</sheetData></worksheet>""",
        )
    return buffer.getvalue()


def _png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
        "de0000000c4944415408d763f8ffff3f0005fe02fea3b2c1b20000000049454e44ae426082"
    )
