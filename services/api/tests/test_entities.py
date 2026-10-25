import pytest

from app.graphstore.entities import (
    normalize_alias,
    resolve_document,
    same_transaction,
    transaction_external_id,
)


def _models_ready() -> bool:
    try:
        from app.models import EntityAlias, ResolvedEntity
    except Exception:
        return False
    return EntityAlias is not None and ResolvedEntity is not None


def test_normalize_alias_folds_case_punctuation_and_space():
    assert normalize_alias("Mock Inference Cloud") == "mock inference cloud"
    assert normalize_alias("mock inference cloud") == "mock inference cloud"
    assert normalize_alias("  Mock,   Inference   Cloud! ") == "mock inference cloud"
    assert normalize_alias("Cloud Services Inc.") == "cloud services inc"
    assert normalize_alias("cloud services inc") == "cloud services inc"
    assert normalize_alias("") == ""
    assert normalize_alias("   ") == ""
    assert normalize_alias("...") == ""
    assert normalize_alias(None) == ""
    assert normalize_alias("Apex Freight") != normalize_alias("Apex Freight LLC")


def test_transaction_external_id_uses_only_transaction_prefixes():
    assert transaction_external_id("PO-C01-1123") == "PO-C01-1123"
    for prefix in ("PO", "INV", "JE", "EM", "MSG", "CTR", "AMD", "SVC", "ACR"):
        assert transaction_external_id(f"{prefix}-1") == f"{prefix}-1"
    for external_id in ("POL-ACC-v1", "CT-1", "UNK-441", "CASE-008", "C01", "", None):
        assert transaction_external_id(external_id) is None


def test_resolve_document_returns_transaction_and_vendor():
    purchase = resolve_document(
        "org-1",
        "PO-C01-1123",
        "purchase_order",
        "Mock Inference Cloud",
        ["PO-C01-1123"],
    )
    invoice = resolve_document(
        "org-1",
        "INV-C01-381",
        "invoice",
        "mock inference cloud",
        ["INV-C01-381", "PO-C01-1123"],
    )
    assert purchase["transaction"]["kind"] == "transaction"
    assert purchase["transaction"]["external_id"] == "PO-C01-1123"
    assert purchase["transaction"]["organization_id"] == "org-1"
    assert purchase["vendor"]["kind"] == "vendor"
    assert purchase["vendor"]["alias"] == "mock inference cloud"
    assert purchase["vendor"]["id"] == invoice["vendor"]["id"]
    assert purchase["vendor"]["external_id"] == invoice["vendor"]["external_id"]
    assert invoice["transaction"]["external_id"] == "INV-C01-381"
    assert purchase["transaction"]["id"] != invoice["transaction"]["id"]

    punctuated = resolve_document("org-1", "EM-1", "email", "Mock   Inference, Cloud!", [])
    assert punctuated["vendor"]["id"] == purchase["vendor"]["id"]

    other_org = resolve_document("org-2", "PO-1", "purchase_order", "Mock Inference Cloud", [])
    assert other_org["vendor"]["id"] != purchase["vendor"]["id"]

    apex = resolve_document("org-1", "PO-510", "purchase_order", "Apex Freight", [])
    apex_llc = resolve_document("org-1", "PO-511", "purchase_order", "Apex Freight LLC", [])
    assert apex["vendor"]["id"] != apex_llc["vendor"]["id"]


def test_empty_vendor_and_non_transaction_id_yield_no_entity():
    policy = resolve_document("org-1", "POL-ACC-v1", "policy", "   ", ["POL-ACC-v1"])
    assert policy == {"transaction": None, "vendor": None}
    unknown = resolve_document("org-1", "UNK-441", "note", "...", [])
    assert unknown["transaction"] is None
    assert unknown["vendor"] is None
    named = resolve_document("org-1", "CASE-008", "case", "Acme", [])
    assert named["transaction"] is None
    assert named["vendor"]["external_id"] == "acme"


def test_same_transaction_uses_id_or_reference_not_case():
    purchase = {"external_id": "PO-C01-1123", "references": ["PO-C01-1123"], "case_ids": ["C01"]}
    invoice = {"external_id": "INV-C01-381", "references": ["INV-C01-381", "PO-C01-1123"], "case_ids": ["C01"]}
    service = {"external_id": "SVC-0001", "references": ["SVC-0001", "PO-C01-1123"], "case_ids": ["C01"]}
    accrual = {"external_id": "ACR-C01-202509", "references": ["ACR-C01-202509"], "case_ids": ["C01"], "entity_ref": "c01"}
    message = {"external_id": "MSG-0001", "references": ["PO-C01-1123"], "case_ids": ["C09"]}
    assert same_transaction(purchase, invoice)
    assert same_transaction(invoice, purchase)
    assert same_transaction(purchase, service)
    assert same_transaction(service, message)
    assert not same_transaction(service, accrual)
    assert not same_transaction(accrual, purchase)

    separate = [
        {"external_id": "PO-A", "references": ["PO-A"], "case_ids": ["CASE-008"], "entity_ref": "vendor-apex"},
        {"external_id": "PO-B", "references": ["PO-B"], "case_ids": ["CASE-008"], "entity_ref": "vendor-apex"},
    ]
    assert not same_transaction(separate[0], separate[1])
    assert same_transaction(
        {"external_id": "PO-C01-1123", "references": [], "case_ids": ["C01"]},
        {"external_id": "PO-C01-1123", "references": [], "case_ids": ["C99"]},
    )
    assert same_transaction(
        {"external_id": "NOTE-1", "references": ["NOTE-2"], "case_ids": []},
        {"external_id": "NOTE-2", "references": [], "case_ids": ["CASE-008"]},
    )


@pytest.mark.skipif(not _models_ready(), reason="ResolvedEntity and EntityAlias are not in app.models yet")
def test_persist_entities_is_idempotent():
    from sqlalchemy import func, select

    from app.db import configure, session
    from app.graphstore.entities import persist_entities
    from app.models import EntityAlias, Organization, ResolvedEntity

    configure("sqlite:///:memory:")
    records = [
        {
            "external_id": "PO-C01-1123",
            "kind": "purchase_order",
            "vendor": "Mock Inference Cloud",
            "references": ["PO-C01-1123"],
        },
        {
            "external_id": "INV-C01-381",
            "kind": "invoice",
            "vendor": "mock inference cloud!",
            "references": ["INV-C01-381", "PO-C01-1123"],
        },
        {"external_id": "POL-ACC-v1", "kind": "policy", "vendor": "", "references": []},
        {
            "external_id": "PO-A",
            "kind": "purchase_order",
            "vendor": "Apex Freight",
            "references": ["PO-A"],
            "case_ids": ["CASE-008"],
        },
        {
            "external_id": "PO-B",
            "kind": "purchase_order",
            "vendor": "Apex Freight LLC",
            "references": ["PO-B"],
            "case_ids": ["CASE-008"],
        },
    ]
    with session() as db:
        db.add(Organization(id="org-1", name="Acme"))
        db.commit()
        first = persist_entities(db, "org-1", records)
        db.commit()
        second = persist_entities(db, "org-1", records)
        db.commit()
        entities = db.scalars(select(ResolvedEntity).where(ResolvedEntity.organization_id == "org-1")).all()
        aliases = db.scalars(select(EntityAlias).where(EntityAlias.organization_id == "org-1")).all()
        entity_count = db.scalar(
            select(func.count()).select_from(ResolvedEntity).where(ResolvedEntity.organization_id == "org-1")
        )
        alias_count = db.scalar(select(func.count()).select_from(EntityAlias).where(EntityAlias.organization_id == "org-1"))

    assert entity_count == 7
    assert alias_count == 3
    assert sorted(item["id"] for item in first["entities"]) == sorted(item["id"] for item in second["entities"])
    assert sorted(item["alias"] for item in first["aliases"]) == sorted(row.alias for row in aliases)
    by_key = {(row.kind, row.external_id): row for row in entities}
    vendor = by_key[("vendor", "mock inference cloud")]
    assert vendor.label == "Mock Inference Cloud"
    assert by_key[("transaction", "PO-A")].id != by_key[("transaction", "PO-B")].id
    assert by_key[("vendor", "apex freight")].id != by_key[("vendor", "apex freight llc")].id
    alias_by_name = {row.alias: row for row in aliases}
    assert alias_by_name["mock inference cloud"].entity_id == vendor.id
    assert len({row.id for row in entities}) == 7
