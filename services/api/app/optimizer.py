"""Deterministic BOM optimizer.

Money is Decimal. Fixed shipping and taxes are charged once per supplier that
receives any units, never once per unit. The search enumerates quote subsets
for each BOM line (seeded catalogs are a handful of offers per component) and
picks the minimum landed cost that satisfies quantity, MOQ, capacity, deadline,
compatibility, and budget.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.compatibility import problem
from app.money import money, money_str

INFINITE_BUDGET = Decimal("1000000000.00")


@dataclass(frozen=True)
class OptLine:
    bom_line_id: str
    component_type: str
    units_per_computer: int
    required_sku: str
    allowed_skus: tuple[str, ...]
    position: int


@dataclass(frozen=True)
class OptQuote:
    quote_id: str
    supplier_id: str
    supplier_name: str
    sku: str
    component_type: str
    unit_price: Decimal
    available_qty: int
    min_order_qty: int
    shipping_amount: Decimal | None
    taxes_amount: Decimal | None
    estimated_arrival: date
    spec: dict


@dataclass
class Allocation:
    bom_line_id: str
    component_type: str
    quote_id: str
    supplier_id: str
    supplier_name: str
    sku: str
    quantity: int
    unit_price: Decimal
    extended_cost: Decimal
    shipping_amount: Decimal
    taxes_amount: Decimal
    estimated_arrival: date
    spec: dict


@dataclass
class Candidate:
    component_type: str
    bom_line_id: str
    sku: str
    spec: dict
    parts: tuple[tuple[str, int], ...]
    quotes: dict
    goods: Decimal
    suppliers: frozenset[str]
    arrival: date


@dataclass
class PlanResult:
    feasible: bool
    lines: list[Allocation] = field(default_factory=list)
    total_landed_cost: Decimal | None = None
    goods_cost: Decimal | None = None
    shipping_by_supplier: dict[str, Decimal] = field(default_factory=dict)
    taxes_by_supplier: dict[str, Decimal] = field(default_factory=dict)
    readiness_computers: int = 0
    per_component_builds: dict[str, int] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    rejected_alternatives: list[dict] = field(default_factory=list)
    binding_constraints: list[str] = field(default_factory=list)
    blocking_components: list[str] = field(default_factory=list)
    explanation: str = ""
    latest_arrival: date | None = None
    plan_hash: str | None = None
    supplier_ids: list[str] = field(default_factory=list)


def _min_cost_fill(quotes: list[OptQuote], required: int):
    dp: list[tuple[Decimal, tuple] | None] = [None] * (required + 1)
    dp[0] = (Decimal("0"), tuple())
    for quote in quotes:
        nxt = dp[:]
        upper = min(quote.available_qty, required)
        if quote.min_order_qty > upper or quote.min_order_qty < 1:
            dp = nxt
            continue
        for have, state in enumerate(dp):
            if state is None:
                continue
            k = quote.min_order_qty
            while k <= upper and have + k <= required:
                cost = state[0] + quote.unit_price * Decimal(k)
                pos = have + k
                choice = state[1] + ((quote.quote_id, k),)
                if nxt[pos] is None or cost < nxt[pos][0]:
                    nxt[pos] = (cost, choice)
                k += 1
        dp = nxt
    return dp[required]


def _charges(parts: list[Allocation]) -> tuple[dict[str, Decimal], dict[str, Decimal], list[str]]:
    shipping: dict[str, Decimal] = {}
    taxes: dict[str, Decimal] = {}
    notes: list[str] = []
    grouped: dict[str, list[Allocation]] = {}
    for part in parts:
        grouped.setdefault(part.supplier_id, []).append(part)
    for supplier_id, rows in grouped.items():
        ship_values = {row.shipping_amount for row in rows}
        tax_values = {row.taxes_amount for row in rows}
        shipping[supplier_id] = max(ship_values)
        taxes[supplier_id] = max(tax_values)
        if len(ship_values) > 1:
            notes.append(
                f"Supplier {rows[0].supplier_name} quoted differing shipping amounts; charged the maximum once."
            )
    return shipping, taxes, notes


def _hash_payload(result: PlanResult, budget: Decimal, deadline: date, currency: str) -> str:
    payload = {
        "budget": money_str(budget),
        "currency": currency,
        "deadline": deadline.isoformat(),
        "lines": [
            {
                "bom_line_id": line.bom_line_id,
                "component_type": line.component_type,
                "quantity": line.quantity,
                "quote_id": line.quote_id,
                "sku": line.sku,
                "supplier_id": line.supplier_id,
                "unit_price": money_str(line.unit_price),
            }
            for line in sorted(result.lines, key=lambda row: (row.component_type, row.supplier_id, row.sku, row.quote_id))
        ],
        "shipping": [
            {"amount": money_str(amount), "supplier_id": supplier_id}
            for supplier_id, amount in sorted(result.shipping_by_supplier.items())
        ],
        "taxes": [
            {"amount": money_str(amount), "supplier_id": supplier_id}
            for supplier_id, amount in sorted(result.taxes_by_supplier.items())
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _explain(result: PlanResult, computers: int, budget: Decimal) -> str:
    if not result.feasible or result.total_landed_cost is None:
        joined = ", ".join(result.binding_constraints) or "no feasible allocation"
        return (
            f"No feasible purchasing plan for {computers} computers. "
            f"Binding constraints: {joined}. "
            f"Complete computers supportable from filtered stock: {result.readiness_computers}."
        )
    chunks = [
        f"{line.quantity} x {line.sku} from {line.supplier_name} at {money_str(line.unit_price)} ({money_str(line.extended_cost)})"
        for line in result.lines
    ]
    shipping = ", ".join(
        f"{next(line.supplier_name for line in result.lines if line.supplier_id == supplier_id)} {money_str(amount)}"
        for supplier_id, amount in sorted(result.shipping_by_supplier.items(), key=lambda item: item[0])
    )
    headroom = budget - result.total_landed_cost
    arrival = result.latest_arrival.isoformat() if result.latest_arrival else "unknown"
    return (
        f"Feasible plan for {computers} computers, readiness {result.readiness_computers}. "
        + "; ".join(chunks)
        + f". Shipping charged once per supplier: {shipping}. "
        + f"Landed cost {money_str(result.total_landed_cost)} against budget {money_str(budget)} "
        + f"(headroom {money_str(headroom)}). Latest arrival {arrival}."
    )


def solve(
    *,
    computer_quantity: int,
    lines: list[OptLine],
    quotes: list[OptQuote],
    budget: Decimal,
    deadline: date,
    rules: list[dict],
    currency: str = "USD",
    max_suppliers: int | None = None,
) -> PlanResult:
    budget = money(budget)
    per_builds: dict[str, int] = {}
    candidates: list[list[Candidate]] = []
    rejected: list[dict] = []
    binding: list[str] = []
    blocking: list[str] = []

    quote_by_id = {quote.quote_id: quote for quote in quotes}
    for line in sorted(lines, key=lambda item: item.position):
        required = computer_quantity * line.units_per_computer
        allowed = set(line.allowed_skus)
        matched = [
            quote
            for quote in quotes
            if quote.component_type == line.component_type and quote.sku in allowed
        ]
        usable: list[OptQuote] = []
        saw_deadline = False
        saw_unknown_ship = False
        saw_unknown_tax = False
        for quote in matched:
            if quote.shipping_amount is None:
                saw_unknown_ship = True
                rejected.append(
                    {
                        "reason": "unknown_shipping",
                        "detail": f"{quote.supplier_name} {quote.sku} did not state shipping",
                        "component_type": line.component_type,
                        "sku": quote.sku,
                        "supplier_id": quote.supplier_id,
                    }
                )
                continue
            if quote.taxes_amount is None:
                saw_unknown_tax = True
                rejected.append(
                    {
                        "reason": "unknown_tax",
                        "detail": f"{quote.supplier_name} {quote.sku} did not state taxes",
                        "component_type": line.component_type,
                        "sku": quote.sku,
                    }
                )
                continue
            if quote.estimated_arrival > deadline:
                saw_deadline = True
                rejected.append(
                    {
                        "reason": "deadline",
                        "detail": f"{quote.supplier_name} {quote.sku} arrives {quote.estimated_arrival.isoformat()} after {deadline.isoformat()}",
                        "component_type": line.component_type,
                        "sku": quote.sku,
                        "supplier_id": quote.supplier_id,
                        "arrival": quote.estimated_arrival.isoformat(),
                    }
                )
                continue
            usable.append(quote)

        by_sku: dict[str, list[OptQuote]] = {}
        for quote in usable:
            by_sku.setdefault(quote.sku, []).append(quote)
        line_candidates: list[Candidate] = []
        seen: set[tuple] = set()
        for sku, group in by_sku.items():
            group = group[:4]
            for mask in range(1, 1 << len(group)):
                subset = [group[i] for i in range(len(group)) if mask & (1 << i)]
                specs = [quote.spec for quote in subset]
                if any(spec != specs[0] for spec in specs):
                    continue
                filled = _min_cost_fill(subset, required)
                if filled is None:
                    continue
                _cost, choice = filled
                if not choice:
                    continue
                key = tuple(sorted(choice))
                if key in seen:
                    continue
                seen.add(key)
                used = []
                suppliers = set()
                arrival = subset[0].estimated_arrival
                for quote_id, qty in choice:
                    quote = quote_by_id[quote_id]
                    used.append(quote)
                    suppliers.add(quote.supplier_id)
                    if quote.estimated_arrival > arrival:
                        arrival = quote.estimated_arrival
                line_candidates.append(
                    Candidate(
                        component_type=line.component_type,
                        bom_line_id=line.bom_line_id,
                        sku=sku,
                        spec=dict(specs[0]),
                        parts=key,
                        quotes={quote.quote_id: quote for quote in used},
                        goods=sum(
                            (quote_by_id[quote_id].unit_price * Decimal(qty) for quote_id, qty in choice),
                            Decimal("0"),
                        ),
                        suppliers=frozenset(suppliers),
                        arrival=arrival,
                    )
                )
        capacity = sum(quote.available_qty for quote in usable)
        per_builds[line.component_type] = capacity // line.units_per_computer if line.units_per_computer else 0
        if not line_candidates:
            blocking.append(line.component_type)
            if not matched:
                reason = "missing_component"
            elif saw_unknown_ship and not usable:
                reason = "unknown_shipping"
            elif saw_unknown_tax and not usable:
                reason = "unknown_tax"
            elif saw_deadline and not usable:
                reason = "deadline"
            elif capacity < required:
                reason = "capacity"
            else:
                reason = "minimum_order"
            binding.append(reason)
            binding.append(f"{line.component_type}:{reason}")
            rejected.append(
                {
                    "reason": reason,
                    "detail": f"{line.component_type} cannot be filled for {required} units",
                    "component_type": line.component_type,
                    "required": required,
                }
            )
        candidates.append(line_candidates)

    readiness = min(per_builds.values()) if per_builds else 0
    if any(not group for group in candidates):
        result = PlanResult(
            feasible=False,
            readiness_computers=readiness,
            per_component_builds=per_builds,
            rejected_alternatives=rejected[:24],
            binding_constraints=_unique(binding),
            blocking_components=blocking,
            assumptions=["Shipping, tax, or currency left unknown is never treated as zero."],
        )
        result.explanation = _explain(result, computer_quantity, budget)
        return result

    best, runners, compat_rejects = _search(candidates, rules, budget, max_suppliers)
    rejected.extend(compat_rejects)
    if best is None:
        unlimited, _, extra_rejects = _search(candidates, rules, INFINITE_BUDGET, max_suppliers)
        rejected.extend(extra_rejects)
        if unlimited is None and max_suppliers is not None:
            unlimited_suppliers, _, _ = _search(candidates, rules, INFINITE_BUDGET, None)
            if unlimited_suppliers is not None:
                binding.append("max_suppliers")
            else:
                binding.append("compatibility")
        elif unlimited is None:
            binding.append("compatibility")
        else:
            goods = unlimited["goods"]
            total = unlimited["total"]
            if goods <= budget < total:
                binding.extend(["shipping", "budget"])
            else:
                binding.append("budget")
            rejected.append(
                {
                    "reason": "budget",
                    "detail": f"cheapest compatible landed cost is {money_str(total)} which exceeds budget {money_str(budget)}",
                    "total": money_str(total),
                }
            )
        result = PlanResult(
            feasible=False,
            readiness_computers=readiness,
            per_component_builds=per_builds,
            rejected_alternatives=rejected[:24],
            binding_constraints=_unique(binding),
            blocking_components=blocking,
            assumptions=["A plan over the task budget is not proposed for approval."],
        )
        result.explanation = _explain(result, computer_quantity, budget)
        return result

    allocations = _allocations(best["chosen"])
    shipping, taxes, notes = _charges(allocations)
    total = money(best["total"])
    goods = money(best["goods"])
    for runner in runners[:5]:
        rejected.append(
            {
                "reason": "more_expensive",
                "detail": runner["summary"],
                "total": money_str(runner["total"]),
                "supplier_count": len(runner["suppliers"]),
            }
        )
    result = PlanResult(
        feasible=True,
        lines=allocations,
        total_landed_cost=total,
        goods_cost=goods,
        shipping_by_supplier={key: money(value) for key, value in shipping.items()},
        taxes_by_supplier={key: money(value) for key, value in taxes.items()},
        readiness_computers=computer_quantity,
        per_component_builds={**per_builds, **{line.component_type: computer_quantity for line in lines}},
        assumptions=[
            "Fixed shipping is charged once per supplier that receives an order, not once per unit.",
            "Taxes stated on the selected quotes are charged once per supplier (the maximum stated amount).",
            "Known zero taxes are explicit. Unknown shipping or tax excludes the quote.",
            *notes,
        ],
        rejected_alternatives=rejected[:24],
        binding_constraints=[],
        latest_arrival=max(line.estimated_arrival for line in allocations),
        supplier_ids=sorted({line.supplier_id for line in allocations}),
    )
    # Readiness of a feasible exact fill is the requested computer count.
    # per_component_builds above reflects that allocation, not merely raw capacity.
    result.plan_hash = _hash_payload(result, budget, deadline, currency)
    result.explanation = _explain(result, computer_quantity, budget)
    return result


def _allocations(chosen: list[Candidate]) -> list[Allocation]:
    rows: list[Allocation] = []
    for candidate in chosen:
        for quote_id, qty in candidate.parts:
            quote = candidate.quotes[quote_id]
            rows.append(
                Allocation(
                    bom_line_id=candidate.bom_line_id,
                    component_type=candidate.component_type,
                    quote_id=quote.quote_id,
                    supplier_id=quote.supplier_id,
                    supplier_name=quote.supplier_name,
                    sku=quote.sku,
                    quantity=qty,
                    unit_price=quote.unit_price,
                    extended_cost=money(quote.unit_price * Decimal(qty)),
                    shipping_amount=quote.shipping_amount if quote.shipping_amount is not None else Decimal("0"),
                    taxes_amount=quote.taxes_amount if quote.taxes_amount is not None else Decimal("0"),
                    estimated_arrival=quote.estimated_arrival,
                    spec=dict(quote.spec),
                )
            )
    return rows


def _search(candidates: list[list[Candidate]], rules: list[dict], budget: Decimal, max_suppliers: int | None):
    best: dict | None = None
    runners: list[dict] = []
    rejects: list[dict] = []
    seen_reject: set[str] = set()

    def rec(index: int, chosen: list[Candidate], goods: Decimal, suppliers: set[str], specs: dict):
        nonlocal best
        if goods > budget and best is None:
            # Still explore: shipping is extra, but goods already over budget cannot win.
            return
        if best is not None and goods > best["total"]:
            return
        if index == len(candidates):
            if max_suppliers is not None and len(suppliers) > max_suppliers:
                return
            allocations = _allocations(chosen)
            shipping, taxes, _notes = _charges(allocations)
            ship_total = sum(shipping.values(), Decimal("0"))
            tax_total = sum(taxes.values(), Decimal("0"))
            total = goods + ship_total + tax_total
            summary = ", ".join(f"{item.sku} via {len(item.suppliers)} supplier(s)" for item in chosen)
            record = {
                "chosen": list(chosen),
                "goods": goods,
                "total": total,
                "suppliers": set(suppliers),
                "summary": summary,
            }
            if total <= budget and (best is None or total < best["total"] or (total == best["total"] and len(suppliers) < len(best["suppliers"]))):
                if best is not None:
                    runners.append(best)
                best = record
            elif best is not None:
                runners.append(record)
            return
        for candidate in candidates[index]:
            trial = dict(specs)
            trial[candidate.component_type] = candidate.spec
            issue = problem(trial, rules)
            if issue:
                label = f"{issue}|{candidate.component_type}|{candidate.sku}"
                if label not in seen_reject and len(rejects) < 16:
                    seen_reject.add(label)
                    rejects.append(
                        {
                            "reason": "compatibility",
                            "detail": issue,
                            "component_type": candidate.component_type,
                            "sku": candidate.sku,
                        }
                    )
                continue
            rec(
                index + 1,
                chosen + [candidate],
                goods + candidate.goods,
                suppliers | set(candidate.suppliers),
                trial,
            )

    rec(0, [], Decimal("0"), set(), {})
    runners.sort(key=lambda item: item["total"])
    return best, runners, rejects


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
