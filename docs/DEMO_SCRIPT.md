# Three-minute demo

**0:00** Acme Corp booked a $150 September accrual for Cloud Services Inc. In December the vendor billed $158. Was the September decision wrong?

**0:30** Open `CASE-001`. Set 30 September 2025, Maya Chen, audit mode. The graph shows purchase order $145, email estimate $160, and accrual $150. The December invoice is absent. Its document date is 28 September, but accounting received it on 10 December, so the September snapshot excludes it.

**1:10** Run the agent. The timeline shows reads of the purchase order, the email, and the accrual, then a denied read of `INV-381` with reason `FUTURE_INFORMATION`. The finding is questionable, says the origin of $150 was not preserved, and proposes human review. It does not say the accountant should have booked $158.

**1:55** Switch to 15 December 2025 as Jordan Hale. The invoice is now inside the snapshot. Auditor view on 30 September can show that later evidence exists, without the amount.

**2:25** Open Before / after. The unrestricted baseline is marked unsafe and uses the $158 invoice. WorldState does not. Approve the proposal as the auditor. The audit log records the review. Nothing is posted to an ERP.

## Excel version

Setup: the API is on port 8000, `npm run dev-server` is running in `apps/excel-addin`, and `fixtures/WorldState_Demo.xlsx` is open with the WorldState pane sideloaded.

**0:00** Open `WorldState_Demo.xlsx` and the WorldState pane from the Home tab. The `Accruals` sheet has ten September accruals. Nothing in it will be changed.

**0:30** Select `Accruals!D3` and click **Inspect**. The pane shows:
- $150.00 and `=B3+C3`
- direct precedents B3 ($100) and C3 ($50), from Excel's own precedent lookup
- a **Review** finding: the Sep 25 vendor email EM-208 quotes $160, a $10 difference. It may be reasonable. It is not called incorrect.

**1:10** Click **Trace**. The graph links B3 and C3 to the two PO-104 lines (verified). It links D3 to PO-104 (verified) and to EM-208 (candidate, conflicts). One node reads "Not yet available". Click EM-208 to read the email and its locator.

**1:45** The as-of date is Sep 30, 2025. The withheld node is invoice INV-104. The response carries no amount, no text, and no date for it. Opening it directly by id returns 403.

**2:15** Switch to Dec 15, 2025. INV-104 ($158) appears with document date Sep 29 and received date Dec 10. Its document date never made it available in September.

**2:40** Select `A1:F11` and click **Scan range**. Three cells are flagged:
- D3: Review.
- D5: Unknown source. The $980 was entered by hand with no source.
- D6: Review. Lattice Office cites Cloud Services' PO-104.

The other seven rows stay as they were, including the euro quote, the October estimate, and the $2 difference inside tolerance. Click **Clear WorldState highlights**. The original fills return, and every value is unchanged.

Reset the local SQLite file by deleting `services/api/worldstate.db` and starting the API again. Docker starts from an empty Postgres volume and seeds on startup.
