# Three-minute demo

**0:00** Acme Corp booked a $150 September accrual for Cloud Services Inc. In December the vendor billed $158. Was the September decision wrong?

**0:30** Open `CASE-001`. Set 30 September 2025, Maya Chen, audit mode. The graph shows purchase order $145, email estimate $160, and accrual $150. The December invoice is absent. Its document date is 28 September, but accounting received it on 10 December, so the September snapshot excludes it.

**1:10** Run the agent. The timeline shows reads of the purchase order, the email, and the accrual, then a denied read of `INV-381` with reason `FUTURE_INFORMATION`. The finding is questionable, says the origin of $150 was not preserved, and proposes human review. It does not say the accountant should have booked $158.

**1:55** Switch to 15 December 2025 as Jordan Hale. The invoice is now inside the snapshot. Auditor view on 30 September can show that later evidence exists, without the amount.

**2:25** Open Before / after. The unrestricted baseline is marked unsafe and uses the $158 invoice. WorldState does not. Approve the proposal as the auditor. The audit log records the review. Nothing is posted to an ERP.

## Excel version

Setup: the API is running and has finished the company ingest, `npm run dev-server` is running in `apps/excel-addin`, and `fixtures/Decagon_Accruals.xlsx` is open with the WorldState pane sideloaded. The pane opens as Avery Lin, accountant, on Sep 30, 2025.

**0:00** The `Accruals` sheet has Decagon's ten September accruals, one per case. The invoice column is missing on purpose: in September, nobody had the invoices yet.

**0:30** Select `F2`, Cloud vendor C01, and click **Inspect**. The pane shows:
- $150.00, posted as `ACR-C01-202509` (ledger `accrual_events.csv:ACR-C01-202509`).
- A **Review** finding: vendor email MSG-0001 quoted $160 on Sep 25, a $10 difference. PO-C01-1123 ($145) also differs. It may be reasonable. It is not called incorrect.
- Origin: no earlier source or sum accounts for $150.
- Two linked sources are withheld.

**1:10** Click **Trace**. The graph links F2 to its posted entry and its PO (verified). It also links the email (it names the PO) and the contract (filed under the vendor), both as candidates, and two "Withheld" nodes. Click MSG-0001 to read the email.

**1:40** Switch to **Dec 15, 2025**. Invoice INV-C01-381 ($158) appears with document date Sep 28 and received date Dec 10. Its printed date never made it available in September. Opening it by id under the September trace returns 403.

**2:10** Back on Sep 30, select `F8` (C07) as Avery. It is **Supported** by PO-C07-1123. Two sources are withheld: the October invoice, and a contract this role is not allowed to read. Switch **Viewing as** to Noah Patel, auditor. Now contract CTR-0007 ($2,100) is visible, and the same cell needs **Review**.

**2:40** As Avery on Sep 30:
1. Select `A1:H11` and click **Scan range**. Seven accruals are flagged. Each one gets a yellow fill and a native note. Hover `F2` to read its note.
2. C06, C07 and C08 are supported, and every PO amount agrees with its purchase order.
3. Click **Clear WorldState highlights and notes**. The fills return, the notes are gone, and every value is unchanged.

Reset the local SQLite file by deleting `services/api/worldstate.db` and starting the API again. Docker starts from an empty Postgres volume and seeds on startup.
