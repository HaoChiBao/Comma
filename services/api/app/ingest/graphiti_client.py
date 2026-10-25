import json
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import GraphEpisode, IngestRecord
from app.temporal import money


def add_episode(db: Session, record: IngestRecord, job_id: str, group_id: str) -> dict:
    body = episode_body(record)
    reference = record.document_date or record.available_from
    if os.environ.get("GRAPHITI_URI"):
        episode_id = _push_graphiti(record, body, reference, group_id)
        backend = "graphiti"
    else:
        episode_id = uuid4().hex
        backend = "graphiti-embedded"
    db.add(
        GraphEpisode(
            id=episode_id,
            job_id=job_id,
            group_id=group_id,
            name=record.external_id,
            body=body,
            source_description=f"{record.kind} extracted from an uploaded document",
            reference_time=reference,
            backend=backend,
            record_id=record.id,
        )
    )
    return {"episode_id": episode_id, "backend": backend, "name": record.external_id}


def episode_body(record: IngestRecord) -> str:
    return json.dumps(
        {
            "document_id": record.external_id,
            "kind": record.kind,
            "vendor": record.vendor,
            "amount": money(record.amount),
            "currency": record.currency,
            "entity_ref": record.entity_ref,
            "references": record.references_json,
            "document_date": record.document_date.isoformat() if record.document_date else None,
            "available_from": record.available_from.isoformat(),
            "formula": record.formula,
            "text": record.text,
        },
        sort_keys=True,
    )


def propose_links(episode_body: dict) -> list[dict]:
    """Suggest links from one episode. This does not call Graphiti or open a connection."""
    document_id = str(episode_body.get("document_id") or "")
    amount = episode_body.get("amount")
    amount_text = "" if amount is None else str(amount)
    proposals = []
    seen: set[tuple[str, str, str]] = set()

    def add(claim_type: str, from_id: str, to_id: str, explanation: str) -> None:
        if not from_id or not to_id:
            return
        key = (claim_type, from_id, to_id)
        if key in seen:
            return
        seen.add(key)
        proposals.append(
            {
                "claim_type": claim_type,
                "from_id": from_id,
                "to_id": to_id,
                "explanation": explanation,
            }
        )

    references: list[str] = []
    for reference in episode_body.get("references") or []:
        ref_id = str(reference or "")
        if ref_id and ref_id not in references:
            references.append(ref_id)
    for ref_id in references:
        # The printed amount stays on the invoice suggestion, so refusing that id refuses the figure.
        if document_id and document_id != ref_id and ref_id.upper().startswith("INV-") and amount_text:
            add("SUPPORTS", document_id, ref_id, f"{document_id} names {ref_id} at {amount_text}.")
        elif document_id and document_id != ref_id:
            add("MENTIONS", document_id, ref_id, f"{document_id} names {ref_id}.")
        # Both ends are this id, so the gate can keep a visible reference when the other ids are not.
        add("MENTIONS", ref_id, ref_id, f"{ref_id} is named in this episode.")
    return proposals


def accept_links(proposals, visible_ids: set[str]) -> list[dict]:
    """Keep a suggestion only when both ends are already visible. Dropped ids stay out of the text."""
    visible = {str(item) for item in visible_ids}
    kept = []
    dropped = []
    for proposal in proposals:
        from_id = str(proposal.get("from_id") or "")
        to_id = str(proposal.get("to_id") or "")
        if from_id in visible and to_id in visible:
            kept.append(proposal)
        else:
            dropped.append(proposal)
    hidden = _hidden_tokens(dropped, visible)
    accepted = []
    for proposal in kept:
        accepted.append(
            {
                "claim_type": str(proposal.get("claim_type") or ""),
                "from_id": str(proposal.get("from_id") or ""),
                "to_id": str(proposal.get("to_id") or ""),
                "explanation": _scrub(str(proposal.get("explanation") or ""), hidden),
            }
        )
    return accepted


def _hidden_tokens(dropped: list[dict], visible: set[str]) -> list[str]:
    tokens: set[str] = set()
    for proposal in dropped:
        for key in ("from_id", "to_id"):
            value = str(proposal.get(key) or "")
            if value and value not in visible:
                tokens.add(value)
        for match in re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", str(proposal.get("explanation") or "")):
            tokens.add(match)
    return sorted(tokens, key=len, reverse=True)


def _scrub(explanation: str, tokens: list[str]) -> str:
    for token in tokens:
        if token.isdigit():
            explanation = re.sub(rf"\b{re.escape(token)}\b", "", explanation)
        else:
            explanation = explanation.replace(token, "")
    return " ".join(explanation.split())


def _push_graphiti(record: IngestRecord, body: str, reference: datetime, group_id: str) -> str:
    import asyncio

    from graphiti_core import Graphiti
    from graphiti_core.nodes import EpisodeType
    from pydantic import BaseModel, Field

    class Transaction(BaseModel):
        """A purchase order, invoice, estimate, or other commercial document."""

        document_id: str = Field(description="The document number, such as PO-101 or INV-381")
        amount: str | None = Field(default=None, description="The monetary amount exactly as printed")
        vendor: str | None = None

    class Decision(BaseModel):
        """A recorded accounting decision, such as an accrual or journal entry."""

        document_id: str
        amount: str | None = None

    class Supports(BaseModel):
        """One document's amount is evidence for another document."""

    class Contradicts(BaseModel):
        """Two amounts refer to the same economic event and do not match."""

    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    graphiti = Graphiti(os.environ["GRAPHITI_URI"], os.environ.get("GRAPHITI_USER", "neo4j"), os.environ.get("GRAPHITI_PASSWORD", ""))

    async def send():
        await graphiti.build_indices_and_constraints()
        result = await graphiti.add_episode(
            name=record.external_id,
            episode_body=body,
            source_description=f"Uploaded {record.kind}",
            reference_time=reference,
            source=EpisodeType.json,
            group_id=group_id.replace("-", "_"),
            entity_types={"Transaction": Transaction, "Decision": Decision},
            edge_types={"Supports": Supports, "Contradicts": Contradicts},
            edge_type_map={("Transaction", "Decision"): ["Supports", "Contradicts"], ("Entity", "Entity"): ["Supports", "Contradicts"]},
            custom_extraction_instructions=(
                "Extract every transaction, amount, and decision. Do not drop a printed monetary amount. "
                "Link documents that share a purchase order, invoice, or vendor. Do not invent a formula."
            ),
        )
        await graphiti.close()
        return result.episode.uuid

    return asyncio.run(send())
