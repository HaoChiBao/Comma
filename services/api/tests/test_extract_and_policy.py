from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from app.adapters.llm import complete_json
from app.adapters.photon import PhotonHQAdapter, SignatureError, messaging_adapter, verify_signature
from app.clock import clock
from app.db import SessionLocal
from app.errors import IntegrationNotConfigured
from app.extract import extract_intake, parse_quote_email
from app.fixtures.catalog import DEMO_REQUEST
from app.models import SandboxDelivery, WorkEvent
from app.settings import settings
from app.state import InvalidTransition, transition
from app.tools import TOOL_NAMES


def test_deterministic_intake_parses_demo_sentence():
    intake = extract_intake(DEMO_REQUEST)
    assert intake.source == "deterministic"
    assert intake.quantity == 100
    assert intake.budget == "200000"
    assert intake.deadline == "2026-11-15"
    assert intake.product == "desktop"
    assert intake.destination is None
    assert intake.missing == []


def test_ambiguous_intake_does_not_invent_numbers():
    intake = extract_intake("Need some computers for the lab")
    assert intake.quantity is None
    assert intake.budget is None
    assert intake.deadline is None
    assert "quantity" in intake.missing
    assert "budget" in intake.missing
    assert "delivery deadline" in intake.missing


def test_quote_parser_flags_uncertainty_and_reads_token_from_body():
    body = "Subject would not matter.\nRFQ-TOKEN: RFQabc\n\nSKU: ACME-GPU-7600\nUnit price: 10\nUnit price: 12\nCurrency: EUR\n"
    parsed = parse_quote_email(body)
    assert parsed.token == "RFQabc"
    assert parsed.source == "deterministic"
    assert "conflicting_unit_price" in parsed.blocks[0].uncertainty
    assert "unsupported_currency" in parsed.blocks[0].uncertainty
    assert "missing_shipping" in parsed.blocks[0].uncertainty
    assert parsed.blocks[0].unit_price is None


def test_llm_adapter_is_unused_without_a_key(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("httpx should not be called")

    monkeypatch.setattr("httpx.post", explode, raising=False)
    settings.llm_mode = "live"
    settings.llm_api_key = ""
    assert complete_json("system", "user") is None
    intake = extract_intake(DEMO_REQUEST)
    assert intake.source == "deterministic"


def test_live_photon_send_fails_closed_without_marking_delivery():
    settings.photon_mode = "live"
    settings.photon_api_key = "placeholder"
    settings.photon_project_id = "placeholder"
    before = _sandbox_count()
    adapter = messaging_adapter()
    assert isinstance(adapter, PhotonHQAdapter)
    with pytest.raises(IntegrationNotConfigured):
        adapter.send_message(to="+15555550100", text="hello", correlation_id="c", company_id=None, idempotency_key="live-should-not-send")
    assert _sandbox_count() == before


def test_signature_rejects_bad_and_stale():
    raw = b'{"message":{"id":"m1"}}'
    with pytest.raises(SignatureError):
        verify_signature(raw, None, None, "secret")
    stamp = str(int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()))
    with pytest.raises(SignatureError) as stale:
        verify_signature(raw, stamp, "v0=00", "secret", now=int(datetime.now(timezone.utc).timestamp()))
    assert stale.value.status == 400


def test_forbidden_tools_are_absent():
    forbidden = {"approve", "issue_purchase_order", "edit_audit", "fabricate_quote", "relax_constraint", "change_identity"}
    assert forbidden.isdisjoint(set(TOOL_NAMES))


def test_invalid_transition_is_audited_and_refused():
    session = SessionLocal()
    from app.models import ProcurementTask

    task = session.scalar(select(ProcurementTask).where(ProcurementTask.public_code == "BETA-1001"))
    with pytest.raises(InvalidTransition):
        transition(
            session,
            task,
            "COMPLETED",
            actor_type="system_agent",
            actor_id=None,
            reason="skip the gateway",
            evidence_refs=["test"],
        )
    session.commit()
    denied = session.scalar(
        select(WorkEvent).where(WorkEvent.task_id == task.id, WorkEvent.action == "transition_denied", WorkEvent.allowed.is_(False))
    )
    assert denied is not None
    assert denied.evidence_refs_json == ["test"]
    session.close()


def test_work_events_are_append_only():
    session = SessionLocal()
    from app.audit import record

    event = record(
        session,
        company_id=None,
        task_id=None,
        actor_type="system_agent",
        actor_id=None,
        action="audit_probe",
        previous_state=None,
        new_state=None,
        reason="seed an append-only row",
        evidence_refs=["probe"],
        allowed=True,
    )
    session.commit()
    with pytest.raises(Exception) as updated:
        session.execute(text("UPDATE work_events SET reason = 'mutated' WHERE id = :id"), {"id": event.id})
        session.commit()
    assert "append-only" in str(updated.value)
    session.rollback()
    with pytest.raises(Exception) as deleted:
        session.execute(text("DELETE FROM work_events WHERE id = :id"), {"id": event.id})
        session.commit()
    assert "append-only" in str(deleted.value)
    session.rollback()
    session.close()


def _sandbox_count() -> int:
    session = SessionLocal()
    try:
        return len(session.scalars(select(SandboxDelivery)).all())
    finally:
        session.close()
