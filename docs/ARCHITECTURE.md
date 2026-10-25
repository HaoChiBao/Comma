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

The offline investigator is deterministic. The compare endpoint also runs an unrestricted baseline that ignores knowledge time. That baseline is labeled unsafe and is not a path to a proposed financial action.

## Limits

- The company, vendors, and documents are synthetic.
- WorldState does not know what a person actually read. It knows what was available.
- There is no live ERP connector and no journal posting.
- Optional model investigation is not wired. The demo does not need a key.
- The iMessage Deep Agents process is a separate channel. It is not allowed to supply historical evidence.
- Auditor view reveals that something arrives later. It does not reveal the hidden amount or text.
