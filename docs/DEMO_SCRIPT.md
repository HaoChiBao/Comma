# Three-minute demo

**0:00** Acme Corp booked a $150 September accrual for Cloud Services Inc. In December the vendor billed $158. Was the September decision wrong?

**0:30** Open `CASE-001`. Set 30 September 2025, Maya Chen, audit mode. The graph shows purchase order $145, email estimate $160, and accrual $150. The December invoice is absent. Its document date is 28 September, but accounting received it on 10 December, so the September snapshot excludes it.

**1:10** Run the agent. The timeline shows reads of the purchase order, the email, and the accrual, then a denied read of `INV-381` with reason `FUTURE_INFORMATION`. The finding is questionable, says the origin of $150 was not preserved, and proposes human review. It does not say the accountant should have booked $158.

**1:55** Switch to 15 December 2025 as Jordan Hale. The invoice is now inside the snapshot. Auditor view on 30 September can show that later evidence exists, without the amount.

**2:25** Open Before / after. The unrestricted baseline is marked unsafe and uses the $158 invoice. WorldState does not. Approve the proposal as the auditor. The audit log records the review. Nothing is posted to an ERP.

Reset the local SQLite file by deleting `services/api/worldstate.db` and starting the API again. Docker starts from an empty Postgres volume and seeds on startup.
