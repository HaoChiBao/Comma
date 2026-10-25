import json

from app.graphstore.store import connect_records


def _record(external_id, version_id, kind="purchase_order", vendor="", references=None):
    return {
        "external_id": external_id,
        "version_id": version_id,
        "kind": kind,
        "vendor": vendor,
        "references": references or [external_id],
        "case_ids": ["C01"],
    }


def test_one_vendor_hub_joins_every_document_that_names_it():
    records = [
        _record("PO-1", "v-po", vendor="Mock Inference Cloud"),
        _record("INV-2", "v-inv", kind="invoice", vendor="mock inference cloud", references=["INV-2", "PO-1"]),
    ]
    nodes, edges = connect_records("org-decagon", records, mentions=[], facts=[])
    vendors = [node for node in nodes if node["type"] == "VENDOR"]
    assert len(vendors) == 1
    filed = [edge for edge in edges if edge["type"] == "FILED_WITH"]
    assert len(filed) == 2
    assert {edge["source"] for edge in filed} == {"artifact:v-po", "artifact:v-inv"}
    assert any(edge["type"] == "CITES" and edge["target"] == "artifact:v-po" for edge in edges)


def test_a_named_record_that_is_not_in_view_has_no_amount():
    records = [
        _record("MSG-0001", "v-msg", kind="email", vendor="Mock Inference Cloud", references=["MSG-0001", "INV-C01-381"]),
    ]
    nodes, edges = connect_records("org-decagon", records, mentions=[], facts=[])
    named = next(node for node in nodes if node["label"] == "INV-C01-381")
    assert named["amount"] is None
    assert named["text"] == ""
    blob = json.dumps({"nodes": nodes, "edges": edges})
    assert "158.00" not in blob
    assert any(edge["type"] == "CITES" and edge["target"] == named["id"] for edge in edges)


def test_every_ledger_locator_is_kept_and_the_amount_is_not():
    records = [_record("PO-1", "v-po", vendor="Northwind")]
    facts = [
        {"version_id": "v-po", "fact_type": "gl_amount", "entity_ref": "PO-1", "source_locator": "general_ledger_lines.csv:a"},
        {"version_id": "v-po", "fact_type": "gl_amount", "entity_ref": "PO-1", "source_locator": "general_ledger_lines.csv:b"},
        {"version_id": "v-po", "fact_type": "gl_amount", "entity_ref": "PO-1", "source_locator": "debit 158.00 USD"},
    ]
    _nodes, edges = connect_records("org-decagon", records, mentions=[], facts=facts)
    ledger = [edge for edge in edges if edge["type"] == "LEDGER"]
    assert len(ledger) == 1
    assert "general_ledger_lines.csv:a" in ledger[0]["explanation"]
    assert "general_ledger_lines.csv:b" in ledger[0]["explanation"]
    assert "158.00" not in ledger[0]["explanation"]


def test_a_mention_links_both_documents():
    records = [
        _record("PO-1", "v-po", vendor="Northwind"),
        _record("INV-2", "v-inv", kind="invoice", vendor="Other Goods", references=["INV-2"]),
    ]
    mentions = [{"version_id": "v-inv", "mentioned_external_id": "PO-1"}]
    _nodes, edges = connect_records("org-decagon", records, mentions=mentions, facts=[])
    assert any(edge["type"] == "MENTIONS" and edge["source"] == "artifact:v-inv" and edge["target"] == "artifact:v-po" for edge in edges)
