import json
import sys

from app.ingest.graphiti_client import accept_links, propose_links


def test_later_invoice_suggestion_is_dropped():
    episode = {
        "document_id": "MSG-0001",
        "references": ["INV-C01-381", "PO-C01-1123"],
        "amount": "158.00",
        "text": "Final invoice INV-C01-381 total 158.00 against PO-C01-1123.",
    }
    proposals = propose_links(episode)
    assert "graphiti_core" not in sys.modules
    proposed = json.dumps(proposals)
    assert "INV-C01-381" in proposed
    assert "PO-C01-1123" in proposed
    assert "158" in proposed
    accepted = accept_links(proposals, {"PO-C01-1123"})
    po_links = [item for item in accepted if item["from_id"] == "PO-C01-1123" and item["to_id"] == "PO-C01-1123"]
    assert po_links
    assert all(item["from_id"] != "INV-C01-381" and item["to_id"] != "INV-C01-381" for item in accepted)
    body = json.dumps(accepted)
    assert "INV-C01-381" not in body
    assert "158" not in body
    assert set(po_links[0]) == {"claim_type", "from_id", "to_id", "explanation"}
