"""The company graph can union every corpus without picking a person."""

import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

os.environ["INGEST_INLINE"] = "1"

DEC = "2025-12-15T12:00:00+00:00"
SEPT = "2025-09-30T23:59:00+00:00"


def client():
    from app.db import session
    from app.main import create_app
    from app.models import Organization
    from app.seed import seed
    from app.temporal import sha256_text, utc

    app = create_app("sqlite:///:memory:")
    now = datetime.now(timezone.utc)
    with session() as db:
        seed(db)
        db.add(Organization(id="org-decagon", name="Decagon"))
        db.flush()
        _version(
            db,
            "INV-DEC-1:v1",
            "invoice",
            "INV-DEC-1",
            "Vendor invoice naming JE-900.",
            "Vendor Invoice",
            "vendor-decagon",
            utc("2025-09-20T00:00:00+00:00"),
            "verified",
            now,
            sha256_text,
        )
        _version(
            db,
            "AMD-DEC-9:v1",
            "contract",
            "AMD-DEC-9",
            "A contract amendment with no shared record.",
            "Contract Amendment",
            "vendor-unrelated",
            utc("2025-09-20T00:00:00+00:00"),
            "verified",
            now,
            sha256_text,
        )
        _version(
            db,
            "INV-DEC-LATER:v1",
            "invoice",
            "INV-DEC-LATER",
            "A later invoice with no shared record.",
            "Later Invoice",
            "vendor-later",
            utc("2025-12-10T00:00:00+00:00"),
            "verified",
            now,
            sha256_text,
        )
        _version(
            db,
            "UNK-DEC:v1",
            "memo",
            "UNK-DEC",
            "Availability was never recorded.",
            "Undated note",
            "vendor-unknown",
            None,
            "unknown",
            now,
            sha256_text,
        )
        db.commit()
    return TestClient(app)


def _version(db, version_id, kind, external_id, text, display_name, entity_ref, available_from, status, now, sha256_text):
    from app.models import ArtifactVersion, SourceArtifact

    db.add(
        SourceArtifact(
            id=f"art-{version_id}",
            organization_id="org-decagon",
            kind=kind,
            source_system="test",
            external_id=external_id,
            title=display_name,
        )
    )
    db.add(
        ArtifactVersion(
            id=version_id,
            artifact_id=f"art-{version_id}",
            version=1,
            content_text=text,
            content_hash=sha256_text(text),
            document_date=available_from or now,
            business_valid_from=None,
            business_valid_to=None,
            available_from=available_from,
            available_to=None,
            ingested_at=now,
            availability_status=status,
            availability_evidence="test",
            metadata_json={"case_ids": [], "entity_ref": entity_ref, "display_name": display_name, "formula": ""},
        )
    )


def _records(body):
    found = {}
    for node in body["nodes"]:
        if node.get("redacted") or not str(node.get("id", "")).startswith("artifact:"):
            continue
        found[node.get("document_id") or node["label"]] = node
    return found


def _ids(body):
    return set(_records(body))


def _between(body, left, right):
    records = _records(body)
    ends = {records[left]["id"], records[right]["id"]}
    return [edge for edge in body["edges"] if {edge["source"], edge["target"]} == ends]


def test_scope_all_unions_corpora_and_links_only_cited_records():
    api = client()
    combined = api.get("/api/v1/company-graph", params={"scope": "all", "as_of": DEC, "mode": "audit"})
    assert combined.status_code == 200, combined.text
    body = combined.json()
    assert body["organization_id"] == "all"
    ids = _ids(body)
    assert "JE-900" in ids
    assert "INV-381" in ids
    assert "INV-DEC-1" in ids
    assert "AMD-DEC-9" in ids
    assert "BETA-1" in ids
    assert "UNK-441" not in ids
    assert "UNK-DEC" not in ids
    invoice = next(node for node in body["nodes"] if node.get("document_id") == "INV-DEC-1")
    amendment = next(node for node in body["nodes"] if node.get("document_id") == "AMD-DEC-9")
    assert invoice["label"] == "Vendor Invoice"
    assert amendment["label"] == "Contract Amendment"
    cited = _between(body, "JE-900", "INV-DEC-1")
    assert any(edge["type"] == "MENTIONS" for edge in cited)
    assert _between(body, "JE-900", "AMD-DEC-9") == []
    assert _between(body, "INV-DEC-1", "AMD-DEC-9") == []
    pair_slots = len(ids) * (len(ids) - 1) / 2
    assert len(body["edges"]) < pair_slots

    september = api.get("/api/v1/company-graph", params={"scope": "all", "as_of": SEPT, "mode": "audit"})
    early = _ids(september.json())
    assert "JE-900" in early
    assert "INV-DEC-1" in early
    assert "INV-381" not in early
    assert "INV-DEC-LATER" not in early
    assert "UNK-441" not in early
    assert "CT-010" in early

    maya = api.get(
        "/api/v1/company-graph",
        params={"organization_id": "org-acme", "actor_id": "actor-maya", "as_of": SEPT, "mode": "audit"},
    )
    assert maya.status_code == 200, maya.text
    maya_ids = _ids(maya.json())
    assert "JE-900" in maya_ids
    assert "CT-010" not in maya_ids
    assert "INV-DEC-1" not in maya_ids
    assert "BETA-1" not in maya_ids

    jordan = api.get(
        "/api/v1/company-graph",
        params={"organization_id": "org-acme", "actor_id": "actor-jordan", "as_of": SEPT, "mode": "audit", "view": "auditor"},
    )
    assert "CT-010" in _ids(jordan.json())

    hidden = api.get(
        "/api/v1/company-graph",
        params={"scope": "all", "as_of": DEC, "mode": "pre_decision"},
    )
    assert "JE-910" not in _ids(hidden.json())
    assert "PO-610" in _ids(hidden.json())

    journal = next(node for node in body["nodes"] if node.get("document_id") == "JE-900")
    review = api.post(
        "/api/v1/company-graph/investigate",
        params={"organization_id": "all", "as_of": DEC, "mode": "audit", "node_id": journal["id"]},
        json={
            "thinking": "JE-900 records 150.00.",
            "action": "The visible records were compared.",
            "lines": [],
            "verdict": "The comparison stays on JE-900.",
        },
    )
    assert review.status_code == 200, review.text
    assert review.json()["steps"][0]["text"]
