"""Resolve documents to vendor and transaction entities and upsert them.

Transaction identity is the document id when its prefix is a transaction
prefix. Vendor identity is the normalized alias. Models are imported when
``app.models`` defines them; until then ``persist_entities`` reports that the
tables are not available and the pure helpers still run.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.temporal import sha256_text

try:
    from app.models import EntityAlias, ResolvedEntity
except Exception:
    # Another change adds these classes. Pure resolution still works without them.
    EntityAlias = None
    ResolvedEntity = None

TRANSACTION_PREFIXES = frozenset({"PO", "INV", "JE", "EM", "MSG", "CTR", "AMD", "SVC", "ACR"})

_LABEL_LIMIT = 300
_EXTERNAL_ID_LIMIT = 80
_ALIAS_LIMIT = 300


def normalize_alias(vendor: str | None) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    An empty string means there is no vendor alias.
    """
    if vendor is None:
        return ""
    text = str(vendor).lower()
    text = "".join(char if char.isalnum() or char.isspace() else "" for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_ALIAS_LIMIT]


def transaction_external_id(external_id: str | None) -> str | None:
    """Document id when its prefix is a transaction prefix, else None.

    The prefix is the text before the first hyphen. ``PO-C01-1123`` is a
    transaction. ``POL-ACC-v1`` and a case id are not.
    """
    if external_id is None:
        return None
    token = str(external_id).strip()
    if not token:
        return None
    prefix = token.split("-", 1)[0]
    if prefix.upper() in TRANSACTION_PREFIXES:
        return token
    return None


def transaction_ids_for(external_id: str | None, references: list[str] | None = None) -> set[str]:
    """Transaction external ids this document attaches to.

    That is its own id when the prefix qualifies, plus any referenced id that
    qualifies. Case ids are never included.
    """
    found: set[str] = set()
    own = transaction_external_id(external_id)
    if own:
        found.add(own)
    for ref in references or []:
        key = transaction_external_id(ref)
        if key:
            found.add(key)
    return found


def same_transaction(left: dict, right: dict) -> bool:
    """Whether two documents attach to the same transaction.

    They do when they share a transaction external id (their own transaction
    id, or a transaction id named in either reference list), or when one
    reference list contains the other's document id. A shared case id does
    not attach them.
    """
    left_id = _document_id(left)
    right_id = _document_id(right)
    left_ids = transaction_ids_for(left_id, _references(left))
    right_ids = transaction_ids_for(right_id, _references(right))
    if left_ids and right_ids and left_ids & right_ids:
        return True
    left_refs = set(_references(left))
    right_refs = set(_references(right))
    if right_id and right_id in left_refs:
        return True
    if left_id and left_id in right_refs:
        return True
    return False


def resolve_document(
    organization_id: str,
    external_id: str,
    kind: str,
    vendor: str | None,
    references: list[str] | None,
) -> dict:
    """Return the transaction entity and vendor entity for one document.

    ``kind`` is the document kind. It is not the entity kind and does not
    decide whether a transaction exists. There is no transaction entity when
    the document id prefix is outside the transaction prefixes. There is no
    vendor entity when the vendor name normalizes to nothing.
    """
    del kind
    token = "" if external_id is None else str(external_id).strip()
    return {
        "transaction": _transaction_entity(organization_id, token),
        "vendor": _vendor_entity(organization_id, vendor),
    }


def persist_entities(db: Session, organization_id: str, records: list[dict]) -> dict:
    """Upsert resolved entities and vendor aliases for these records.

    Running this again with the same records does not insert duplicates.
    The first label for an entity is kept. ``records`` may be document dicts
    (``external_id``, ``kind``, ``vendor``, ``references``) or dicts already
    returned by ``resolve_document``.
    """
    if ResolvedEntity is None or EntityAlias is None:
        raise RuntimeError(
            "ResolvedEntity and EntityAlias are not on app.models yet. "
            "persist_entities upserts resolved_entities and entity_aliases once those classes exist."
        )

    entities: dict[tuple[str, str], ResolvedEntity] = {}
    aliases: dict[str, EntityAlias] = {}

    for record in records:
        resolved = _coerce(organization_id, record)
        vendor = resolved.get("vendor")
        if vendor:
            _upsert_vendor(db, organization_id, vendor, entities, aliases)
        transaction = resolved.get("transaction")
        if transaction:
            _upsert_entity(db, organization_id, transaction, entities)
    db.flush()
    return {
        "entities": [_entity_payload(row) for row in entities.values()],
        "aliases": [_alias_payload(row) for row in aliases.values()],
    }


def _transaction_entity(organization_id: str, external_id: str) -> dict | None:
    key = transaction_external_id(external_id)
    if key is None:
        return None
    stored = key[:_EXTERNAL_ID_LIMIT]
    return {
        "id": _entity_id(organization_id, "transaction", stored),
        "organization_id": organization_id,
        "kind": "transaction",
        "external_id": stored,
        "label": stored[:_LABEL_LIMIT],
    }


def _vendor_entity(organization_id: str, vendor: str | None) -> dict | None:
    alias = normalize_alias(vendor)
    if not alias:
        return None
    external_id = _vendor_external_id(alias)
    label = re.sub(r"\s+", " ", str(vendor).strip())[:_LABEL_LIMIT] or alias
    return {
        "id": _entity_id(organization_id, "vendor", external_id),
        "organization_id": organization_id,
        "kind": "vendor",
        "external_id": external_id,
        "label": label,
        "alias": alias,
    }


def _vendor_external_id(alias: str) -> str:
    if len(alias) <= _EXTERNAL_ID_LIMIT:
        return alias
    return sha256_text(alias)[:_EXTERNAL_ID_LIMIT]


def _entity_id(organization_id: str, kind: str, external_id: str) -> str:
    prefix = "txn" if kind == "transaction" else "ven"
    digest = sha256_text(f"{organization_id}|{kind}|{external_id}")
    return f"{prefix}_{digest[:36]}"


def _alias_id(organization_id: str, alias: str) -> str:
    return f"als_{sha256_text(f'{organization_id}|{alias}')[:36]}"


def _coerce(organization_id: str, record: dict) -> dict:
    # resolve_document results always include "transaction". Source records
    # carry the vendor name as a string and have no transaction key.
    if "transaction" in record:
        vendor = record.get("vendor")
        if isinstance(vendor, str):
            vendor = _vendor_entity(organization_id, vendor)
        return {"transaction": record.get("transaction"), "vendor": vendor}
    return resolve_document(
        organization_id,
        record.get("external_id") or "",
        record.get("kind") or "",
        record.get("vendor") or "",
        record.get("references") or [],
    )


def _document_id(record: dict) -> str:
    if record.get("external_id"):
        return str(record["external_id"]).strip()
    transaction = record.get("transaction") or {}
    if transaction.get("external_id"):
        return str(transaction["external_id"]).strip()
    return ""


def _references(record: dict) -> list[str]:
    return [str(item).strip() for item in (record.get("references") or []) if str(item).strip()]


def _upsert_vendor(db: Session, organization_id: str, vendor: dict, entities: dict, aliases: dict) -> None:
    alias = vendor["alias"]
    if alias in aliases:
        return
    existing_alias = db.scalar(
        select(EntityAlias).where(
            EntityAlias.organization_id == organization_id,
            EntityAlias.alias == alias,
        )
    )
    if existing_alias is not None:
        aliases[alias] = existing_alias
        row = db.get(ResolvedEntity, existing_alias.entity_id)
        if row is not None:
            entities[(row.kind, row.external_id)] = row
        return
    row = _upsert_entity(db, organization_id, vendor, entities)
    aliases[alias] = _upsert_alias(db, organization_id, row.id, alias)


def _upsert_entity(db: Session, organization_id: str, spec: dict, entities: dict) -> ResolvedEntity:
    external_id = spec["external_id"][:_EXTERNAL_ID_LIMIT]
    key = (spec["kind"], external_id)
    if key in entities:
        return entities[key]
    row = db.scalar(
        select(ResolvedEntity).where(
            ResolvedEntity.organization_id == organization_id,
            ResolvedEntity.kind == spec["kind"],
            ResolvedEntity.external_id == external_id,
        )
    )
    if row is None:
        row = ResolvedEntity(
            id=spec["id"][:64],
            organization_id=organization_id,
            kind=spec["kind"],
            external_id=external_id,
            label=(spec.get("label") or external_id)[:_LABEL_LIMIT],
        )
        db.add(row)
        db.flush()
    entities[key] = row
    return row


def _upsert_alias(db: Session, organization_id: str, entity_id: str, alias: str) -> EntityAlias:
    stored = alias[:_ALIAS_LIMIT]
    row = db.scalar(
        select(EntityAlias).where(
            EntityAlias.organization_id == organization_id,
            EntityAlias.alias == stored,
        )
    )
    if row is None:
        row = EntityAlias(
            id=_alias_id(organization_id, stored)[:64],
            organization_id=organization_id,
            entity_id=entity_id,
            alias=stored,
        )
        db.add(row)
        db.flush()
    return row


def _entity_payload(row: ResolvedEntity) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "kind": row.kind,
        "external_id": row.external_id,
        "label": row.label,
    }


def _alias_payload(row: EntityAlias) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "entity_id": row.entity_id,
        "alias": row.alias,
    }
