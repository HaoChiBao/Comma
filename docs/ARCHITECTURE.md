# Architecture

WorldState stores immutable source versions, historical access grants, and a frozen snapshot manifest. Search, document lookup, the graph, and the investigator all use that same allowlist. A frontend filter is not the control.

A version is visible when all of these hold:

- It belongs to the actor's organization.
- `availability_status` is `verified` and `available_from` is known.
- `as_of` falls inside the availability interval. The document date is not availability.
- A read grant for the actor's role covers `as_of`.
- In pre-decision mode, the recorded decision version is removed.

Unknown availability is excluded. Ingestion time does not move a verified availability date. A policy governs a case only when its business-effective interval contains the decision time and the policy version is in the snapshot.

The graph draws `DERIVED_FROM` only for the seeded formula on purchase order `PO-220` version 1 (`unit_price * estimated_quantity`). The $150 accrual has no formula, so its origin is unexplained. Close amounts are not treated as a derivation.

## Excel cell traces

The Excel add-in sends one cell's context to `POST /v1/traces/inspect`: address, raw value, formula, direct precedents, `as_of`, and `actor_id`. It does not send the workbook. Formula lineage (`FORMULA_DEPENDS_ON`) comes only from Office.js precedents. The server labels it verified and never infers it.

**The registry.** The Decagon accruals workbook registers itself in `workbook_cells` once the company ingest completes (`ingest/decagon_workbook.py`). Each registered cell keeps its row's accrual, PO, and invoice ids on the server.

**Evidence** comes from what ingest stored, not from hand-written links (`app/workbook.py`, `find_links`):
- `verified`: the documents with the row's own ids. The posted accrual journal is `RECORDED_AS`; it is the entry being checked, not support for it.
- `candidate`: documents that mention the PO or the invoice (`mentions`), and contracts filed under the row's vendor (resolved aliases). Contracts never name the PO.

**Time and role filter.** Every linked document passes through one organization-wide snapshot (`create_org_snapshot`, `guarded_version`). The snapshot is frozen per actor and `as_of`, and is not tied to a case. A source outside it returns only its id and the denial reason. Ledger rows are facts on their document's version, so they are sealed with it.

**Origin.** The stored origin claim for an accrual (`claims`: `derived_from`, `source_candidate`, `unexplained`) is shown only when `knowable_from` is no later than `as_of` and every piece of its evidence is in the snapshot manifest. Otherwise the whole claim is dropped.

**Comparisons** use `Decimal` and require the same currency, and the same period when the source states one. The tolerance is the larger of $1 and 1% (`WORLDSTATE_TOLERANCE_ABS`, `WORLDSTATE_TOLERANCE_PCT`). Statuses:
- **Review**: the cell differs from its posted entry, or an available source differs beyond tolerance.
- **Supported**: a verified source, such as the row's purchase order, agrees with the cell.
- **Unknown**: only candidate evidence agrees, or nothing available accounts for the amount.
- An unregistered formula cell takes the most serious status of its registered inputs.

**Annotations.** Highlights change fill color only. Notes are written only on flagged cells and start with a WorldState marker. The add-in keeps ledgers of the fills it replaced and the notes it wrote in the workbook's add-in settings. **Clear** restores the fills and deletes only notes that still carry the marker.

The offline investigator is deterministic. The compare endpoint also runs an unrestricted baseline that ignores knowledge time. That baseline is labeled unsafe and is not a path to a proposed financial action.

## Limits

- The company, vendors, and documents are synthetic.
- WorldState does not know what a person actually read. It knows what was available.
- There is no live ERP connector and no journal posting.
- Optional model investigation is not wired. The demo does not need a key.
- The iMessage Deep Agents process is a separate channel. It is not allowed to supply historical evidence.
- Auditor view reveals that something arrives later. It does not reveal the hidden amount or text.
