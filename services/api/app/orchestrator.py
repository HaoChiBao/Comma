import logging

from sqlalchemy.orm import Session

from app.adapters.llm import complete_json
from app.audit import record
from app.clock import clock
from app.db import SessionLocal
from app.gateway import queue_photon
from app.models import ProcurementTask
from app.state import transition
from app.tools import TOOL_NAMES, ToolContext, execute

log = logging.getLogger("procurement.agent")

STOP_STATUSES = {
    "NEEDS_INFO",
    "COMPLETED",
    "CANCELLED",
    "AWAITING_APPROVAL",
    "ORDERING",
    "AWAITING_CONFIRMATION",
}


class DeterministicPlanner:
    """Bounded tool policy. Price arithmetic stays in the optimizer, not in this planner."""

    def choose(self, obs: dict) -> dict | None:
        if "task" not in obs:
            return {"tool": "get_task", "input": {"task_id": obs["task_id"]}}
        task = obs["task"]
        if task["status"] in STOP_STATUSES:
            return None
        if "suppliers" not in obs:
            return {"tool": "list_known_suppliers", "input": {"task_id": task["id"]}}
        if "catalog" not in obs:
            return {"tool": "get_supplier_catalog", "input": {"task_id": task["id"]}}
        if "valid_quotes" not in obs:
            return {"tool": "get_valid_quotes", "input": {"task_id": task["id"]}}
        gaps = obs.get("rfq_gaps") or []
        if gaps:
            return {"tool": "send_rfq", "input": {"task_id": task["id"], "supplier_id": gaps[0]["supplier_id"]}}
        if "pending_rfqs" not in obs:
            return {"tool": "list_pending_rfqs", "input": {"task_id": task["id"]}}
        if obs["pending_rfqs"]:
            return None
        if "plan" not in obs:
            return {"tool": "calculate_purchase_plan", "input": {"task_id": task["id"]}}
        if not obs["plan"].get("feasible"):
            return None
        if not obs.get("proposed"):
            return {"tool": "propose_plan", "input": {"task_id": task["id"]}}
        if obs.get("proposed") and not obs.get("approval"):
            return {
                "tool": "request_approval",
                "input": {"task_id": task["id"], "plan_id": obs["proposed"]["id"]},
            }
        return None


class LLMPlanner:
    def choose(self, obs: dict) -> dict | None:
        parsed = complete_json(
            "Choose the next procurement tool. Reply JSON {tool, input} or {tool: null}. "
            f"Allowed tools: {', '.join(TOOL_NAMES)}. Never approve or issue a purchase order.",
            str(obs),
        )
        if not parsed or not parsed.get("tool"):
            return None
        if parsed["tool"] not in TOOL_NAMES:
            return None
        return {"tool": parsed["tool"], "input": parsed.get("input") or {"task_id": obs.get("task_id")}}


def make_planner():
    from app.settings import settings

    if settings.llm_mode == "live" and settings.llm_api_key:
        return LLMPlanner()
    return DeterministicPlanner()


def advance(task_id) -> None:
    session = SessionLocal()
    try:
        task = session.get(ProcurementTask, task_id)
        if task is None:
            session.rollback()
            return
        if task.status in STOP_STATUSES:
            session.commit()
            return
        ctx = ToolContext(company_id=task.company_id)
        planner = make_planner()
        obs: dict = {"task_id": str(task.id)}
        for _ in range(24):
            action = planner.choose(obs)
            if action is None:
                break
            obs = execute(session, ctx, obs, action)
        _finalize(session, ctx, obs)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("advance failed for %s", task_id)
        raise
    finally:
        session.close()


def _finalize(session: Session, ctx: ToolContext, obs: dict) -> None:
    if "task" not in obs:
        return
    task = session.get(ProcurementTask, obs["task"]["id"])
    if task is None or task.status in STOP_STATUSES:
        return
    pending = obs.get("pending_rfqs") or []
    gaps = obs.get("rfq_gaps") or []
    if gaps or pending or obs.get("last_rfq"):
        if not obs.get("approval"):
            if task.status not in ("AWAITING_QUOTES", "BLOCKED"):
                _move(session, task, "AWAITING_QUOTES", "waiting on supplier quotes", obs)
            if obs.get("last_rfq") and not obs.get("sourcing_note_sent"):
                queue_photon(
                    session,
                    task,
                    text=(
                        f"{task.public_code}: I found stale or missing quotes and queued RFQs in the email sandbox. "
                        "Nothing was delivered to a real inbox. I'll plan once the replies are in."
                    ),
                    correlation_id=task.public_code,
                    idem=f"photon:sourcing:{task.id}",
                )
            if pending or gaps:
                return
    plan = obs.get("plan")
    if plan and not plan.get("feasible"):
        explanation = plan.get("explanation") or "no feasible plan"
        changed = task.blocked_reason != explanation
        task.blocked_reason = explanation
        task.updated_at = clock.now()
        if task.status != "BLOCKED":
            _move(session, task, "BLOCKED", explanation, obs)
        elif changed:
            record(
                session,
                company_id=task.company_id,
                task_id=task.id,
                actor_type="system_agent",
                actor_id=None,
                action="plan_infeasible",
                previous_state="BLOCKED",
                new_state="BLOCKED",
                reason=explanation,
                evidence_refs=plan.get("binding_constraints") or [],
                allowed=True,
            )
        if changed:
            queue_photon(
                session,
                task,
                text=f"{task.public_code} is blocked. {explanation} I will not relax budget, deadline, quantity, or compatibility unless you change them.",
                correlation_id=task.public_code,
                idem=f"photon:blocked:{task.id}:{plan.get('hash') or explanation[:40]}",
            )
        return
    if obs.get("approval"):
        if task.status in ("SOURCING", "AWAITING_QUOTES"):
            _move(session, task, "EVALUATING", "quotes are complete", obs)
        if task.status != "AWAITING_APPROVAL":
            _move(session, task, "AWAITING_APPROVAL", "approval requested for the current plan version", obs)
        task.blocked_reason = None
        return


def _move(session, task, status, reason, obs) -> None:
    if task.status == status:
        return
    evidence = []
    if obs.get("approval"):
        evidence.append(obs["approval"]["id"])
    if obs.get("proposed"):
        evidence.append(obs["proposed"]["id"])
    transition(
        session,
        task,
        status,
        actor_type="system_agent",
        actor_id=None,
        reason=reason,
        evidence_refs=evidence,
    )
