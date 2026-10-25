"""Summarize one node from a graph that was already filtered by time and role.

The summary is written from that graph. A hidden document is not loaded, and a
model may only rephrase the brief. Any amount or id it adds is discarded.
"""

from __future__ import annotations

import os
import re

import httpx

_MONEY = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")
_ID = re.compile(r"\b(?:PO|INV|JE|EM|POL|CT|MSG|CTR|AMD|SVC|ACR|CASE)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")

_KIND = {
    "PURCHASE_ORDER": "purchase order",
    "INVOICE": "invoice",
    "EMAIL": "email",
    "JOURNAL": "journal entry",
    "DECISION": "recorded decision",
    "POLICY": "policy",
    "POLICY_VERSION": "policy",
    "CONTRACT": "contract",
    "DOCUMENT": "document",
    "VENDOR": "vendor",
    "TRANSACTION": "transaction",
    "REDACTED": "withheld document",
}

_RISK = {"CONTRADICTS", "AMBIGUOUS", "SOURCE_CANDIDATE"}


def summarize_node(graph: dict, node_id: str, texts: dict[str, str] | None = None) -> dict | None:
    """Read the selected node and the visible structure around it.

    `texts` may supply the full body for a node id that is already in `graph`.
    A node that is absent from `graph` is never read.
    """
    node = _node(graph, node_id)
    if node is None:
        return None
    direct = _direct_links(graph, node_id)
    focused, other_cases = _split(node, direct)
    peers = _hub_peers(graph, node, direct)
    matter = _matter(_dedupe(focused + peers), node)
    bodies = texts or {}
    brief = {
        "node_id": node_id,
        "label": node.get("label") or "This record",
        "what": _what(node, bodies),
        "relationships": _relationships(node, other_cases),
        "connected_to": _connected(matter, bodies),
        "risks": _risks(node, matter),
    }
    brief["summary"] = narrate(brief)
    return brief


def reading_ids(graph: dict, node_id: str) -> list[str]:
    """Version-bearing node ids the summary is allowed to read.

    Every id is already a node in the visible graph.
    """
    node = _node(graph, node_id)
    if node is None:
        return []
    direct = _direct_links(graph, node_id)
    focused, _other = _split(node, direct)
    peers = _hub_peers(graph, node, direct)
    ordered = [node_id]
    for link in _dedupe(focused + peers):
        other_id = str(link["node"].get("id") or "")
        if other_id and other_id not in ordered:
            ordered.append(other_id)
    return [item for item in ordered if item.startswith("artifact:")]


def narrate(brief: dict) -> str:
    """Rephrase the brief when a model is configured. Otherwise return the brief itself."""
    prose = _prose(brief)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return prose
    model = os.environ.get("OPENROUTER_MODEL", "").strip() or "openai/gpt-4o-mini"
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Leave the brief as a few plain sentences. Do not add a document, "
                            "a formula, an amount, or any comment about why it matters."
                        ),
                    },
                    {"role": "user", "content": prose},
                ],
            },
            timeout=12,
        )
        response.raise_for_status()
        text = str(response.json()["choices"][0]["message"]["content"]).strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return prose
    if not text or not _grounded(text, prose):
        return prose
    return text


def _prose(brief: dict) -> str:
    return " ".join(
        part
        for part in (brief["what"], brief["relationships"], brief["connected_to"], brief["risks"])
        if part
    )


def _grounded(text: str, source: str) -> bool:
    """A paraphrase may not introduce an amount or document id absent from the brief."""
    allowed_money = set(_MONEY.findall(source))
    allowed_ids = set(_ID.findall(source))
    if any(amount not in allowed_money for amount in _MONEY.findall(text)):
        return False
    return all(found in allowed_ids for found in _ID.findall(text))


_DATE = re.compile(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}\b")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
_REVIEW_KEYS = ("THINKING", "ACTION", "VERDICT")


def review_in_view(graph: dict, text: str) -> bool:
    """A review may name only amounts and record ids already on the visible graph.

    A difference between two visible amounts is allowed, because the review
    states that gap. A withheld node contributes neither its amount nor its id.
    """
    amounts: list[float] = []
    labels: list[str] = []
    for node in graph.get("nodes") or []:
        if node.get("redacted"):
            continue
        labels.append(str(node.get("label") or ""))
        labels.append(str(node.get("case_id") or ""))
        raw = node.get("amount")
        if raw in (None, ""):
            continue
        try:
            amounts.append(round(float(str(raw).replace(",", "")), 2))
        except ValueError:
            continue
    for case in graph.get("cases") or []:
        labels.append(str(case.get("id") or ""))
        labels.append(str(case.get("vendor") or ""))
    allowed_money = {f"{value:.2f}" for value in amounts}
    for index, left in enumerate(amounts):
        for right in amounts[index + 1 :]:
            allowed_money.add(f"{abs(left - right):.2f}")
    allowed_ids = set(_ID.findall(" ".join(labels)))
    for amount in _MONEY.findall(text):
        if f"{float(amount.replace(',', '')):.2f}" not in allowed_money:
            return False
    return all(found in allowed_ids for found in _ID.findall(text))


def compose_review(graph: dict, node_id: str, brief: dict) -> dict:
    """Turn a finished fact sheet into the three paragraphs the screen shows.

    ``missing`` means the node is not in this graph. ``outside`` means the fact
    sheet names an amount or record the graph does not show. The model is not
    called in either case.
    """
    node = _node(graph, node_id)
    if node is None:
        return {"status": "missing"}
    source = "\n".join(
        [
            str(brief.get("thinking") or ""),
            str(brief.get("action") or ""),
            *[str(line) for line in brief.get("lines") or []],
            str(brief.get("verdict") or ""),
        ]
    )
    if not review_in_view(graph, source):
        return {"status": "outside"}
    narrated = narrate_review(brief)
    lines = [str(line) for line in brief.get("lines") or []]
    return {
        "status": "ok",
        "narrated": narrated is not None,
        "steps": [
            {"kind": "Thinking", "text": narrated["THINKING"] if narrated else brief.get("thinking") or ""},
            {"kind": "Action", "text": narrated["ACTION"] if narrated else brief.get("action") or "", "lines": lines},
            {"kind": "Verdict", "text": narrated["VERDICT"] if narrated else brief.get("verdict") or ""},
        ],
    }


def narrate_review(brief: dict) -> dict | None:
    """Rephrase a review whose records were already chosen.

    The model does not select records, amounts, or suggestions. None means the
    caller should keep the brief's own sentences.
    """
    source = "\n".join(
        part
        for part in (
            str(brief.get("thinking") or ""),
            str(brief.get("action") or ""),
            *[str(line) for line in brief.get("lines") or []],
            str(brief.get("verdict") or ""),
        )
        if part
    )
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or not source:
        return None
    model = os.environ.get("OPENROUTER_MODEL", "").strip() or "openai/gpt-4o-mini"
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "temperature": 0.6,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Rewrite a review that was already calculated. The records, amounts, "
                            "and dates are fixed. Write three short paragraphs: what disagrees, "
                            "which records were compared, and what follows. Use only names, amounts, "
                            "and dates from the fact sheet. Do not add a record, a formula, a person, "
                            "a mailbox, or a confidence score. If a match is not proof of the source, "
                            "keep that. If a record is knowable after a date, keep that date. "
                            "Reply in exactly this shape:\n"
                            "THINKING\n<one paragraph>\nACTION\n<one paragraph>\nVERDICT\n<one paragraph>"
                        ),
                    },
                    {"role": "user", "content": source},
                ],
            },
            timeout=12,
        )
        response.raise_for_status()
        text = str(response.json()["choices"][0]["message"]["content"]).strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None
    parsed = _review_sections(text)
    if parsed is None:
        return None
    rewritten = "\n".join(parsed.values())
    if _PERCENT.search(rewritten) or not _grounded(rewritten, source) or not _dates_grounded(rewritten, source):
        return None
    return parsed


def _review_sections(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parts: dict[str, list[str]] = {}
    current = ""
    for line in cleaned.splitlines():
        key = line.strip().upper().rstrip(":")
        if key in _REVIEW_KEYS:
            current = key
            parts[current] = []
            continue
        if current:
            parts[current].append(line.strip())
    parsed = {key: " ".join(parts.get(key) or []).strip() for key in _REVIEW_KEYS}
    if any(not parsed[key] for key in _REVIEW_KEYS):
        return None
    return parsed


def _dates_grounded(text: str, source: str) -> bool:
    allowed = set(_DATE.findall(source))
    return all(found in allowed for found in _DATE.findall(text))


_STRUCTURAL = {"FILED_WITH", "ATTACHED", "CITES", "MENTIONS", "LEDGER", "DERIVED_FROM", "SOURCE_CANDIDATE", "GOVERNS", "RECONCILES_WITH", "AMBIGUOUS"}


def _what(node: dict, texts: dict[str, str]) -> str:
    label = node.get("label") or "This record"
    if node.get("redacted"):
        return f"{label} is outside this snapshot. The amount and text are withheld."
    kind = _kind_name(node)
    if kind == "vendor":
        return f"{label} is a vendor. Documents filed with it name this vendor."
    if kind == "transaction":
        return f"{label} is a transaction. The attached documents are its records."
    amount = node.get("amount")
    money = f" for ${amount}" if amount else ""
    sentence = f"{label} is {_kind_phrase(kind)}{money}."
    excerpt = _line(node, texts)
    if excerpt:
        sentence += f" It says: {excerpt}."
    return sentence


def _relationships(node: dict, other_cases: list[dict]) -> str:
    if not any(link["type"] == "CONTRADICTS" for link in other_cases):
        return ""
    label = node.get("label") or "This record"
    return f"Other cases also contradict {label}."


def _connected(matter: list[dict], texts: dict[str, str]) -> str:
    sentences = []
    for link in matter:
        other = link["node"]
        label = other.get("label") or "A connected record"
        if other.get("redacted"):
            sentences.append(f"{label} is withheld.")
            continue
        kind = _kind_name(other)
        amount = other.get("amount")
        money = f" for ${amount}" if amount else ""
        sentence = f"{label} is {_kind_phrase(kind)}{money}"
        excerpt = _line(other, texts)
        if excerpt and kind not in {"vendor", "transaction"}:
            sentence += f" and says: {excerpt}"
        sentences.append(sentence + ".")
    return " ".join(sentences)


def _risks(node: dict, matter: list[dict]) -> str:
    amounts = []
    if node.get("amount") and not node.get("redacted"):
        amounts.append(str(node.get("amount")))
    for link in matter:
        if link["type"] not in _RISK or link["node"].get("redacted"):
            continue
        amount = link["node"].get("amount")
        if amount:
            amounts.append(str(amount))
    if len(set(amounts)) < 2:
        if str(node.get("type") or "") in {"JOURNAL", "DECISION"} and node.get("origin") == "unexplained":
            label = node.get("label") or "This record"
            return f"No earlier visible amount explains {label}."
        return ""
    return "The amounts disagree."


def _node(graph: dict, node_id: str) -> dict | None:
    return next((item for item in graph.get("nodes") or [] if item.get("id") == node_id), None)


def _by_id(graph: dict) -> dict:
    return {item.get("id"): item for item in graph.get("nodes") or []}


def _direct_links(graph: dict, node_id: str) -> list[dict]:
    by_id = _by_id(graph)
    links = []
    for edge in graph.get("edges") or []:
        if edge.get("source") != node_id and edge.get("target") != node_id:
            continue
        other_id = edge.get("target") if edge.get("source") == node_id else edge.get("source")
        other = by_id.get(other_id)
        if other is None:
            continue
        links.append(
            {
                "type": str(edge.get("type") or "LINK"),
                "other": str(other.get("label") or other_id),
                "explanation": " ".join(str(edge.get("explanation") or "").split()),
                "node": other,
            }
        )
    return links


def _split(node: dict, links: list[dict]) -> tuple[list[dict], list[dict]]:
    focused = []
    other_cases = []
    for link in links:
        other = link["node"]
        different_case = _other_case(node, other)
        if different_case and link["type"] in _RISK:
            other_cases.append(link)
            continue
        if link["type"] in _STRUCTURAL or link["type"] in _RISK or not different_case or other.get("type") in {"VENDOR", "TRANSACTION"}:
            focused.append(link)
    return focused, other_cases


def _hub_peers(graph: dict, node: dict, links: list[dict]) -> list[dict]:
    """Other documents on the same vendor or transaction hub, limited to this case."""
    by_id = _by_id(graph)
    peers = []
    seen = {node.get("id")}
    for link in links:
        hub = link["node"]
        if hub.get("type") not in {"VENDOR", "TRANSACTION"}:
            continue
        want = "FILED_WITH" if hub.get("type") == "VENDOR" else "ATTACHED"
        hub_id = hub.get("id")
        for edge in graph.get("edges") or []:
            if edge.get("type") != want or hub_id not in {edge.get("source"), edge.get("target")}:
                continue
            other_id = edge.get("target") if edge.get("source") == hub_id else edge.get("source")
            other = by_id.get(other_id)
            if other is None or other.get("id") in seen or other.get("type") in {"VENDOR", "TRANSACTION"}:
                continue
            if _other_case(node, other):
                continue
            if node.get("case_id") and not _same_case(node, other):
                continue
            seen.add(other.get("id"))
            peers.append(
                {
                    "type": want,
                    "other": str(other.get("label") or other_id),
                    "explanation": f"{other.get('label') or 'This record'} is also {want.lower().replace('_', ' ')} {hub.get('label')}.",
                    "node": other,
                }
            )
            if len(peers) >= 6:
                return peers
    return peers


def _dedupe(links: list[dict]) -> list[dict]:
    chosen = []
    seen: set[str] = set()
    for link in links:
        other_id = str(link["node"].get("id") or "")
        if not other_id or other_id in seen:
            continue
        seen.add(other_id)
        chosen.append(link)
    return chosen


def _other_case(left: dict, right: dict) -> bool:
    first = left.get("case_id") or ""
    second = right.get("case_id") or ""
    return bool(first) and bool(second) and first != second


def _same_case(left: dict, right: dict) -> bool:
    first = left.get("case_id") or ""
    return bool(first) and first == (right.get("case_id") or "")


def _matter(links: list[dict], node: dict) -> list[dict]:
    """A few records that share this case, transaction, or citation."""
    label = node.get("label") or ""
    ranked = sorted(links, key=lambda link: _rank(link["type"]))
    chosen = []
    for link in ranked:
        other = link["node"]
        if other.get("type") in {"VENDOR", "TRANSACTION"}:
            continue
        if other.get("label") == label:
            continue
        if link["type"] not in _RISK | {"CITES", "MENTIONS", "ATTACHED", "DERIVED_FROM", "GOVERNS"}:
            continue
        chosen.append(link)
        if len(chosen) == 3:
            break
    return chosen


def _rank(edge_type: str) -> int:
    order = {"CONTRADICTS": 0, "CITES": 1, "MENTIONS": 2, "DERIVED_FROM": 3, "AMBIGUOUS": 4, "SOURCE_CANDIDATE": 5, "ATTACHED": 6, "GOVERNS": 7}
    return order.get(edge_type, 9)


def _line(node: dict, texts: dict[str, str]) -> str:
    text = _excerpt(node, texts, 180)
    parts = [part.strip() for part in text.split(". ") if part.strip()]
    label = str(node.get("label") or "")
    while parts and (parts[0].lower().startswith("from ") or parts[0] == label or parts[0].endswith(label)):
        parts = parts[1:]
    chosen = parts[0] if parts else text
    return chosen[:110].rstrip(".")


def _excerpt(node: dict, texts: dict[str, str], limit: int) -> str:
    raw = texts.get(str(node.get("id") or "")) or node.get("text") or ""
    text = " ".join(str(raw).split())
    return text[:limit].rstrip(".")


def _kind_name(node: dict) -> str:
    return _KIND.get(str(node.get("type") or ""), "record")


def _kind_phrase(kind: str) -> str:
    article = "an" if kind[:1].lower() in "aeiou" else "a"
    return f"{article} {kind}"


def _when(value: str | None) -> str:
    if not value:
        return ""
    return str(value).replace("T", " ")[:16]
