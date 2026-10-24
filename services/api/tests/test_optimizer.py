"""Optimizer unit tests. Expected money is derived from fixture fields."""

from datetime import date, timedelta
from decimal import Decimal
from itertools import product

from app.fixtures.catalog import BOM, BUDGET, RULES, SUPPLIERS
from app.money import money
from app.optimizer import OptLine, OptQuote, solve

ANCHOR = date(2026, 10, 24)
DEADLINE = date(2026, 11, 15)


def _lines(rows=None):
    source = rows if rows is not None else BOM
    built = []
    for index, row in enumerate(source):
        built.append(
            OptLine(
                bom_line_id=f"line-{index}-{row['component_type']}",
                component_type=row["component_type"],
                units_per_computer=row["units_per_computer"],
                required_sku=row["required_sku"],
                allowed_skus=tuple(row["allowed"]),
                position=index,
            )
        )
    return built


def _catalog_quotes(today=ANCHOR):
    quotes = []
    number = 0
    for supplier in SUPPLIERS:
        for line in supplier["lines"]:
            number += 1
            quotes.append(
                OptQuote(
                    quote_id=f"q{number}",
                    supplier_id=supplier["code"],
                    supplier_name=supplier["name"],
                    sku=line["sku"],
                    component_type=line["component"],
                    unit_price=money(line["price"]),
                    available_qty=line["qty"],
                    min_order_qty=line["moq"],
                    shipping_amount=money(line["shipping"]),
                    taxes_amount=money("0.00"),
                    estimated_arrival=today + timedelta(days=line["lead"]),
                    spec=dict(line["spec"]),
                )
            )
    return quotes


def _lookup(name, sku):
    for supplier in SUPPLIERS:
        if supplier["name"] != name:
            continue
        for line in supplier["lines"]:
            if line["sku"] == sku:
                return money(line["price"]), money(line["shipping"])
    raise AssertionError(f"missing fixture {name} {sku}")


WINNING = [
    ("gpu", "ACME-GPU-7600", "Northwind Components", 100),
    ("cpu", "ACME-CPU-LGA", "Kepler CPUs", 100),
    ("motherboard", "ACME-MB-H610", "Pinnacle Boards", 100),
    ("ram", "ACME-RAM-DDR5-16", "Lattice Memory", 200),
    ("storage", "ACME-SSD-1TB", "Redwood Storage", 100),
    ("psu", "ACME-PSU-750", "Voltworks Power", 100),
    ("case", "ACME-CASE-MATX", "MicroShell", 100),
    ("cooling", "ACME-COOL-LGA", "FrostPeak Cooling", 100),
]


def expected_from_picks(picks):
    goods = Decimal("0.00")
    shipping = Decimal("0.00")
    charged = set()
    for _component, sku, name, qty in picks:
        price, ship = _lookup(name, sku)
        goods += price * qty
        if name not in charged:
            shipping += ship
            charged.add(name)
    return money(goods), money(shipping), money(goods + shipping)


def test_feasible_full_bom_total_from_fixtures():
    goods, shipping, total = expected_from_picks(WINNING)
    assert total < BUDGET
    assert total != money("192000.00")
    result = solve(
        computer_quantity=100,
        lines=_lines(),
        quotes=_catalog_quotes(),
        budget=BUDGET,
        deadline=DEADLINE,
        rules=RULES,
    )
    assert result.feasible is True
    assert result.readiness_computers == 100
    chosen = {(line.component_type, line.sku, line.supplier_name, line.quantity) for line in result.lines}
    assert chosen == set(WINNING)
    assert result.goods_cost == goods
    assert sum(result.shipping_by_supplier.values(), Decimal("0")) == shipping
    assert result.total_landed_cost == total
    assert all(money(amount) == amount for amount in result.shipping_by_supplier.values())
    assert "Helios Silicon" not in {line.supplier_name for line in result.lines}
    assert any(item["reason"] == "deadline" for item in result.rejected_alternatives)
    assert any(item["reason"] == "compatibility" for item in result.rejected_alternatives)


def test_shipping_charged_once_per_supplier():
    """Supplier A quotes GPU and CPU with a $40 fee on each line. The fee is charged once."""
    lines = [
        OptLine("g", "gpu", 1, "GPU", ("GPU",), 0),
        OptLine("c", "cpu", 1, "CPU", ("CPU",), 1),
    ]
    quotes = [
        OptQuote("ag", "A", "A", "GPU", "gpu", money("90"), 5, 1, money("40"), money("0"), date(2026, 11, 1), {}),
        OptQuote("ac", "A", "A", "CPU", "cpu", money("90"), 5, 1, money("40"), money("0"), date(2026, 11, 1), {}),
        OptQuote("bg", "B", "B", "GPU", "gpu", money("80"), 5, 1, money("40"), money("0"), date(2026, 11, 1), {}),
        OptQuote("cc", "C", "C", "CPU", "cpu", money("80"), 5, 1, money("40"), money("0"), date(2026, 11, 1), {}),
    ]
    result = solve(
        computer_quantity=1,
        lines=lines,
        quotes=quotes,
        budget=money("1000"),
        deadline=date(2026, 11, 15),
        rules=[],
    )
    assert result.feasible is True
    assert {line.supplier_id for line in result.lines} == {"A"}
    assert result.shipping_by_supplier == {"A": money("40")}
    assert result.total_landed_cost == money("220.00")
    split = money("80") + money("80") + money("40") + money("40")
    assert result.total_landed_cost < split


def test_incompatible_cpu_motherboard_rejected():
    lines = [
        OptLine("c", "cpu", 1, "CPU-LGA", ("CPU-LGA", "CPU-AM5"), 0),
        OptLine("m", "motherboard", 1, "MB-AM5", ("MB-AM5",), 1),
    ]
    quotes = [
        OptQuote("lga", "kepler", "Kepler", "CPU-LGA", "cpu", money("10"), 5, 1, money("1"), money("0"), date(2026, 11, 1), {"socket": "LGA1700"}),
        OptQuote("am5", "north", "North", "CPU-AM5", "cpu", money("50"), 5, 1, money("1"), money("0"), date(2026, 11, 1), {"socket": "AM5"}),
        OptQuote("mb", "pin", "Pinnacle", "MB-AM5", "motherboard", money("20"), 5, 1, money("2"), money("0"), date(2026, 11, 1), {"socket": "AM5"}),
    ]
    result = solve(
        computer_quantity=1,
        lines=lines,
        quotes=quotes,
        budget=money("500"),
        deadline=date(2026, 11, 15),
        rules=[RULES[0]],
    )
    assert result.feasible is True
    assert {line.sku for line in result.lines} == {"CPU-AM5", "MB-AM5"}
    assert result.total_landed_cost == money("73.00")
    assert any("socket" in item["detail"] for item in result.rejected_alternatives if item["reason"] == "compatibility")


def test_omitted_component_infeasible():
    rows = [row for row in BOM if row["component_type"] != "cooling"]
    rows.append({**next(row for row in BOM if row["component_type"] == "cooling")})
    quotes = [quote for quote in _catalog_quotes() if quote.component_type != "cooling"]
    result = solve(
        computer_quantity=100,
        lines=_lines(BOM),
        quotes=quotes,
        budget=BUDGET,
        deadline=DEADLINE,
        rules=RULES,
    )
    assert result.feasible is False
    assert "cooling" in result.blocking_components
    assert "missing_component" in result.binding_constraints
    assert "cooling:missing_component" in result.binding_constraints


def test_ram_shortage_blocks_while_gpu_stock_is_sufficient():
    quotes = []
    for quote in _catalog_quotes():
        if quote.supplier_id == "lattice":
            quotes.append(_replace_qty(quote, 190))
        elif quote.supplier_id == "summit":
            quotes.append(_replace_qty(quote, 5))
        else:
            quotes.append(quote)
    result = solve(
        computer_quantity=100,
        lines=_lines(),
        quotes=quotes,
        budget=BUDGET,
        deadline=DEADLINE,
        rules=RULES,
    )
    assert result.feasible is False
    assert "ram:capacity" in result.binding_constraints
    assert result.per_component_builds["ram"] < 100
    assert result.per_component_builds["gpu"] >= 100


def test_infeasible_budget_deadline_moq_and_unknown_shipping():
    quotes = _catalog_quotes()
    tight = solve(
        computer_quantity=100,
        lines=_lines(),
        quotes=quotes,
        budget=money("100.00"),
        deadline=DEADLINE,
        rules=RULES,
    )
    assert tight.feasible is False
    assert "budget" in tight.binding_constraints

    late = solve(
        computer_quantity=100,
        lines=_lines(),
        quotes=quotes,
        budget=BUDGET,
        deadline=date(2026, 10, 25),
        rules=RULES,
    )
    assert late.feasible is False
    assert "deadline" in late.binding_constraints

    moq_lines = [OptLine("g", "gpu", 1, "GPU", ("GPU",), 0)]
    moq_quotes = [
        OptQuote("m", "S", "S", "GPU", "gpu", money("10"), 100, 50, money("1"), money("0"), date(2026, 11, 1), {}),
    ]
    moq = solve(
        computer_quantity=10,
        lines=moq_lines,
        quotes=moq_quotes,
        budget=money("10000"),
        deadline=date(2026, 11, 15),
        rules=[],
    )
    assert moq.feasible is False
    assert "minimum_order" in moq.binding_constraints

    unknown = [
        OptQuote("u", "S", "S", "GPU", "gpu", money("10"), 100, 1, None, money("0"), date(2026, 11, 1), {}),
    ]
    missing_ship = solve(
        computer_quantity=10,
        lines=moq_lines,
        quotes=unknown,
        budget=money("10000"),
        deadline=date(2026, 11, 15),
        rules=[],
    )
    assert missing_ship.feasible is False
    assert "unknown_shipping" in missing_ship.binding_constraints
    assert missing_ship.total_landed_cost is None


def test_two_computer_exhaustive_enumeration():
    """Independent enumerator. It does not call the optimizer's candidate generator."""
    lines = [
        OptLine("g", "gpu", 1, "GPU", ("GPU",), 0),
        OptLine("c", "cpu", 1, "CPU", ("CPU",), 1),
    ]
    quotes = [
        OptQuote("g1", "A", "A", "GPU", "gpu", money("10"), 2, 1, money("5"), money("0"), date(2026, 11, 1), {}),
        OptQuote("g2", "B", "B", "GPU", "gpu", money("9"), 1, 1, money("4"), money("0"), date(2026, 11, 1), {}),
        OptQuote("c1", "A", "A", "CPU", "cpu", money("7"), 2, 1, money("5"), money("0"), date(2026, 11, 1), {}),
        OptQuote("c2", "C", "C", "CPU", "cpu", money("6"), 2, 1, money("4"), money("0"), date(2026, 11, 1), {}),
    ]
    enumerated = _exhaustive(lines, quotes, computers=2, budget=money("1000"))
    result = solve(
        computer_quantity=2,
        lines=lines,
        quotes=quotes,
        budget=money("1000"),
        deadline=date(2026, 11, 15),
        rules=[],
    )
    assert enumerated is not None
    assert result.feasible is True
    assert result.total_landed_cost == enumerated


def _replace_qty(quote: OptQuote, qty: int) -> OptQuote:
    return OptQuote(
        quote.quote_id,
        quote.supplier_id,
        quote.supplier_name,
        quote.sku,
        quote.component_type,
        quote.unit_price,
        qty,
        quote.min_order_qty,
        quote.shipping_amount,
        quote.taxes_amount,
        quote.estimated_arrival,
        quote.spec,
    )


def _exhaustive(lines, quotes, computers, budget):
    grouped = []
    for line in lines:
        required = computers * line.units_per_computer
        offers = [quote for quote in quotes if quote.component_type == line.component_type and quote.sku in line.allowed_skus]
        options = []
        for mask in product(*[range(offer.available_qty + 1) for offer in offers]):
            if sum(mask) != required:
                continue
            if any(qty != 0 and qty < offer.min_order_qty for qty, offer in zip(mask, offers)):
                continue
            goods = sum((offer.unit_price * qty for qty, offer in zip(mask, offers)), Decimal("0"))
            suppliers = {}
            for qty, offer in zip(mask, offers):
                if qty == 0:
                    continue
                suppliers[offer.supplier_id] = max(suppliers.get(offer.supplier_id, Decimal("0")), offer.shipping_amount)
            options.append((goods, suppliers))
        grouped.append(options)
    best = None
    for combo in product(*grouped):
        goods = sum((item[0] for item in combo), Decimal("0"))
        shipping = {}
        for _goods, suppliers in combo:
            for supplier_id, amount in suppliers.items():
                shipping[supplier_id] = max(shipping.get(supplier_id, Decimal("0")), amount)
        total = money(goods + sum(shipping.values(), Decimal("0")))
        if total <= budget and (best is None or total < best):
            best = total
    return best
