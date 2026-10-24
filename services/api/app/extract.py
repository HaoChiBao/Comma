import re
from dataclasses import dataclass, field
from datetime import date, datetime

from app.clock import clock

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}


@dataclass
class Intake:
    quantity: int | None
    budget: str | None
    deadline: str | None
    destination: str | None
    product: str | None
    missing: list[str] = field(default_factory=list)
    source: str = "deterministic"


@dataclass
class QuoteBlock:
    sku: str | None
    unit_price: str | None
    currency: str | None
    available_qty: int | None
    min_order_qty: int | None
    shipping_amount: str | None
    taxes_amount: str | None
    estimated_arrival: str | None
    valid_until: str | None
    uncertainty: list[str] = field(default_factory=list)


@dataclass
class QuoteEmail:
    token: str | None
    blocks: list[QuoteBlock]
    source: str = "deterministic"


def extract_intake(text: str) -> Intake:
    from app.adapters.llm import complete_json

    live = complete_json(
        "Extract a procurement intake as JSON with keys quantity, budget, deadline, destination, product.",
        text,
    )
    if isinstance(live, dict) and live.get("quantity"):
        intake = Intake(
            quantity=_int_or_none(live.get("quantity")),
            budget=str(live["budget"]) if live.get("budget") is not None else None,
            deadline=str(live["deadline"]) if live.get("deadline") else None,
            destination=live.get("destination"),
            product=live.get("product"),
            source="llm",
        )
        intake.missing = _missing(intake)
        return intake
    return _deterministic_intake(text)


def _deterministic_intake(text: str) -> Intake:
    quantity = None
    match = re.search(r"(\d{1,6})\s+(?:complete\s+)?(?:desktop\s+)?computers\b", text, re.I)
    if match is None:
        match = re.search(r"(\d{1,6})\s+desktops\b", text, re.I)
    if match:
        quantity = int(match.group(1))

    budget = None
    match = re.search(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.\d{2})?", text)
    if match:
        budget = match.group(1).replace(",", "")
    if budget is None:
        match = re.search(r"\bbudget\b[^0-9]{0,16}([0-9][0-9,]*(?:\.\d{2})?)", text, re.I)
        if match:
            budget = match.group(1).replace(",", "")

    deadline = _deadline(text)
    destination = None
    match = re.search(
        r"\b(?:ship\s+to|deliver\s+to|destination)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 ,.'-]{2,60})",
        text,
        re.I,
    )
    if match and not re.match(r"by\b|before\b", match.group(1), re.I):
        destination = match.group(1).strip(" .")

    product = None
    if re.search(r"\b(desktop\s+computers?|complete\s+desktops?|desktops?|computers?)\b", text, re.I):
        product = "desktop"

    intake = Intake(quantity, budget, deadline, destination, product, source="deterministic")
    intake.missing = _missing(intake)
    return intake


def _missing(intake: Intake) -> list[str]:
    missing = []
    if intake.product != "desktop":
        missing.append("product")
    if not intake.quantity:
        missing.append("quantity")
    if not intake.budget:
        missing.append("budget")
    if not intake.deadline:
        missing.append("delivery deadline")
    return missing


def _deadline(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1)
    match = re.search(
        r"\b(?:by|before|on|delivered by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?",
        text,
        re.I,
    )
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    day = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else _infer_year(month, day)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _infer_year(month: int, day: int) -> int:
    today = clock.today()
    candidate = date(today.year, month, day)
    if candidate < today:
        return today.year + 1
    return today.year


def parse_quote_email(body: str) -> QuoteEmail:
    from app.adapters.llm import complete_json

    live = complete_json("Extract supplier quote blocks as JSON.", body)
    if isinstance(live, dict) and isinstance(live.get("blocks"), list) and live["blocks"]:
        blocks = [_block_from_mapping(item) for item in live["blocks"]]
        return QuoteEmail(token=live.get("token"), blocks=blocks, source="llm")
    return _deterministic_quotes(body)


def _deterministic_quotes(body: str) -> QuoteEmail:
    token_match = re.search(r"RFQ-TOKEN:\s*([A-Za-z0-9_-]+)", body)
    token = token_match.group(1) if token_match else None
    parts = re.split(r"(?=^SKU:\s*)", body, flags=re.M)
    blocks: list[QuoteBlock] = []
    for part in parts:
        if not re.search(r"^SKU:\s*", part, re.M):
            continue
        blocks.append(_parse_block(part))
    return QuoteEmail(token=token, blocks=blocks, source="deterministic")


def _parse_block(part: str) -> QuoteBlock:
    sku_match = re.search(r"SKU:\s*([A-Z0-9-]+)", part, re.I)
    prices = re.findall(r"Unit price:\s*([0-9]+(?:\.[0-9]+)?)", part, re.I)
    currencies = re.findall(r"Currency:\s*([A-Za-z]{3})", part)
    qtys = re.findall(r"Available qty:\s*(\d+)", part, re.I)
    moqs = re.findall(r"MOQ:\s*(\d+)", part, re.I)
    ships = re.findall(r"Shipping:\s*([0-9]+(?:\.[0-9]+)?)", part, re.I)
    taxes = re.findall(r"Taxes:\s*([0-9]+(?:\.[0-9]+)?)", part, re.I)
    arrivals = re.findall(r"Arrival:\s*(20\d{2}-\d{2}-\d{2})", part, re.I)
    valids = re.findall(r"Valid until:\s*(20\d{2}-\d{2}-\d{2})", part, re.I)
    uncertainty: list[str] = []
    if not sku_match:
        uncertainty.append("missing_sku")
    if not prices:
        uncertainty.append("missing_unit_price")
    elif len(set(prices)) > 1:
        uncertainty.append("conflicting_unit_price")
    if not currencies:
        uncertainty.append("missing_currency")
    elif any(item.upper() != "USD" for item in currencies):
        uncertainty.append("unsupported_currency")
    if not qtys:
        uncertainty.append("missing_available_qty")
    if not moqs:
        uncertainty.append("missing_moq")
    if not ships:
        uncertainty.append("missing_shipping")
    if not taxes:
        uncertainty.append("missing_taxes")
    if not arrivals:
        uncertainty.append("missing_arrival")
    elif not _valid_date(arrivals[0]):
        uncertainty.append("invalid_arrival")
    if not valids:
        uncertainty.append("missing_valid_until")
    elif not _valid_date(valids[0]):
        uncertainty.append("invalid_valid_until")
    return QuoteBlock(
        sku=sku_match.group(1).upper() if sku_match else None,
        unit_price=prices[0] if prices and len(set(prices)) == 1 else None,
        currency=currencies[0].upper() if currencies else None,
        available_qty=int(qtys[0]) if qtys else None,
        min_order_qty=int(moqs[0]) if moqs else None,
        shipping_amount=ships[0] if ships else None,
        taxes_amount=taxes[0] if taxes else None,
        estimated_arrival=arrivals[0] if arrivals and _valid_date(arrivals[0]) else None,
        valid_until=valids[0] if valids and _valid_date(valids[0]) else None,
        uncertainty=uncertainty,
    )


def _block_from_mapping(item: dict) -> QuoteBlock:
    uncertainty = list(item.get("uncertainty") or [])
    block = QuoteBlock(
        sku=item.get("sku"),
        unit_price=str(item["unit_price"]) if item.get("unit_price") is not None else None,
        currency=item.get("currency"),
        available_qty=_int_or_none(item.get("available_qty")),
        min_order_qty=_int_or_none(item.get("min_order_qty")),
        shipping_amount=str(item["shipping_amount"]) if item.get("shipping_amount") is not None else None,
        taxes_amount=str(item["taxes_amount"]) if item.get("taxes_amount") is not None else None,
        estimated_arrival=item.get("estimated_arrival"),
        valid_until=item.get("valid_until"),
        uncertainty=uncertainty,
    )
    if not block.uncertainty and not block.unit_price:
        block.uncertainty.append("missing_unit_price")
    return block


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def render_quote_reply(token: str, lines: list[dict]) -> str:
    chunks = [f"RFQ-TOKEN: {token}", "This is a sandbox supplier reply."]
    for line in lines:
        chunks.append(
            "\n".join(
                [
                    f"SKU: {line['sku']}",
                    f"Unit price: {line['unit_price']}",
                    "Currency: USD",
                    f"Available qty: {line['available_qty']}",
                    f"MOQ: {line['min_order_qty']}",
                    f"Shipping: {line['shipping_amount']}",
                    f"Taxes: {line['taxes_amount']}",
                    f"Arrival: {line['estimated_arrival']}",
                    f"Valid until: {line['valid_until']}",
                ]
            )
        )
    return "\n\n".join(chunks)
