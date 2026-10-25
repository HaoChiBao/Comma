# Three-minute demo

**0:00** Acme Corp booked a $150 September accrual for Cloud Services Inc. In December the vendor billed $158. Was the September decision wrong?

**0:30** Open `CASE-001`. Set 30 September 2025, Maya Chen, audit mode. The graph shows purchase order $145, email estimate $160, and accrual $150. The December invoice is absent. Its document date is 28 September, but accounting received it on 10 December, so the September snapshot excludes it.

**1:10** Run the agent. The timeline shows reads of the purchase order, the email, and the accrual, then a denied read of `INV-381` with reason `FUTURE_INFORMATION`. The finding is questionable, says the origin of $150 was not preserved, and proposes human review. It does not say the accountant should have booked $158.

**1:55** Switch to 15 December 2025 as Jordan Hale. The invoice is now inside the snapshot. Auditor view on 30 September can show that later evidence exists, without the amount.

**2:25** Open Before / after. The unrestricted baseline is marked unsafe and uses the $158 invoice. WorldState does not. Approve the proposal as the auditor. The audit log records the review. Nothing is posted to an ERP.

## Excel version

Setup: finish the company ingest, generate `fixtures/Decagon_Accruals.xlsx`, start the API and add-in dev server, then open the workbook in Excel with the WorldState pane sideloaded.

**0:00** The idle view shows the current selection, an **Inspect** button, and cells flagged by a bounded scan of the active sheet. The generated `Accruals` sheet has ten September accruals. It leaves invoice amounts out because those invoices were not available at close.

**0:30** Select `F2` (Cloud Services Inc., $150) and click **Inspect F2**. The result shows the API's **Review** status and explanation, the cell's source evidence, formula context, and ring graph. The PO (`PO-C01-1123`, $145) and email (`MSG-0001`, $160) are available; later or role-restricted sources appear without identifying details. Use **Open source** on the selected email to read it.

**1:10** Switch the snapshot to **Dec 15, 2025**. The December invoice (`INV-C01-381`, $158) now appears in the graph and sourced timeline, with a Dec 10 receipt date.

**1:40** Switch **Viewing as** to Noah Patel, auditor, then back to Avery Lin, accountant. Inspect `F8` (C07) to compare the role-scoped evidence. The finding status always matches the API response.

**2:10** Return to Avery and Sep 30. Select `A1:H11` and click **Scan selection & add notes**. Seven accruals are flagged and highlighted; each receives a native note. Hover `F2` to read its note. Click **Clear WorldState highlights and notes**; original fills return, WorldState notes are deleted, and values remain unchanged.

Reset the local SQLite file by deleting `services/api/worldstate.db` and starting the API again. Docker starts from an empty Postgres volume and seeds on startup.
