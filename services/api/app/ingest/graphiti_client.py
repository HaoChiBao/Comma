import json
import os
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
