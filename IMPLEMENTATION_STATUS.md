# Implementation status

Date of this run: 2026-10-24.

## Starting point

The repository was README-only on `main`. No application stack existed. No Photon project id, API key, webhook secret, SMTP account, or LLM key was available. Placeholders live in `.env.example` only.

What can be shown without external services: the full 8-component, 100-computer flow. Intake, RFQs, quote extraction, the optimizer, approval, purchase orders, supplier acknowledgement, disruption, and the audit log all run against local Postgres. Outbound iMessage and email stay in the sandbox ledger.

## Implemented

- Multi-tenant tasks, BOM, compatibility rules, suppliers, quotes, plans, approvals, purchase orders, outbox, and append-only `work_events`.
- Deterministic intake and quote parser. A live chat-completions adapter exists and returns immediately unless `LLM_MODE=live` and `LLM_API_KEY` are both set.
- Photon Stable signature check and inbound `messages` parsing. Mock outbound ledger. Live send fails closed.
- Sandbox email with HMAC inbound webhooks. Subject is not used for correlation.
- Optimizer with `Decimal` money, shipping and tax once per supplier, compatibility, deadline, MOQ, capacity, and budget.
- Approval gateway is the only purchase-order writer. Duplicate and concurrent approvals create one logical PO per supplier and plan.
- Worker leases, `SKIP LOCKED`, and outbox reconcile for failed or ambiguous delivery.
- Demo routes 404 when demo mode is off or `APP_ENV=production`.
- Next.js console and a Playwright pass of the demo script.

## Mocked

- iMessage delivery (`PHOTON_MODE=mock`). Sandbox rows say `simulated: true` and `delivered_to_network: false`.
- Supplier email (`EMAIL_MODE=sandbox`). The local webhook contract is not a Gmail or vendor API.
- Supplier replies, the RAM disruption, and purchase-order acknowledgements are demo controls.

## Blocked

- Live Photon send. Stable docs (photon.codes/docs, 2026-10-24) have no public HTTP send endpoint. `space.send` via `spectrum-ts` needs a project id and key, which were not provided.
- Live email. No SMTP credentials.
- Live LLM. No API key. The demo does not call one.

P2 (voice, negotiation, supplier discovery, policy learning, CRM, ERP) is out of scope.

## Verification

Backend, from `services/api`, against `procurement_test` on 2026-10-24 after the audit fixes:

```bash
cd /workspace/services/api
DATABASE_URL=postgresql+psycopg://acme:acme@localhost:5432/procurement_test PYTHONPATH=. /tmp/procurement-venv/bin/python -m pytest --tb=line --junitxml=/tmp/pytest-junit.xml -q
```

Exit code 0. JUnit attributes: `tests=43` `failures=0` `errors=0` `skipped=0` `time=28.708`.

Breakdown: `test_acceptance.py` 16, `test_audit_trail.py` 6, `test_extract_and_policy.py` 9, `test_optimizer.py` 7, `test_stress.py` 5.

Audit checks covered by that run:

- Invalid transitions raise and leave the task status unchanged. The denial is a new `work_events` row with actor, timestamp, previous state, new state, reason, and evidence. A second denial appends another row.
- `GET /api/tasks/{id}/events` matches the stored rows, including a requester approval denial.
- Cancelling a task is audited. A later approval creates no purchase order. A requester who did not create the task is denied and the status stays put.
- Expired and stale approvals record previous state `pending` and the resulting `expired` or `invalidated` state.
- A 50-stick RAM cut invalidates the previous plan and its approval. The new plan still covers 200 RAM sticks and 100 GPUs.
- Webhook redelivery of the same supplier email does not increase the quote count.
- A duplicate approval writes `approval_replayed` and does not add a purchase order. Concurrent manager and controller approvals still create one PO per supplier.
- A purchase order moving `SENDING` to `SENT` records `SENDING` as the previous state.
- Tenant B approving or cancelling tenant A's task returns 404, leaves A's task and orders unchanged, and writes the denial on B's company with no task id and without A's public code.
- Demo mutation routes return 404 when `APP_ENV=production` and when `DEMO_MODE` is false. `/health` still returns 200.

Remaining holes:

- Denials with no task state to show (unknown phone, forbidden tool, cross-tenant id) store `previous_state` null and `new_state` `denied`. They do not copy another tenant's status into the caller's audit row.
- The append-only trigger blocks `UPDATE` and `DELETE` of `work_events`. `TRUNCATE`, which demo reset uses, is not blocked by that row trigger.
- `EMAIL_MODE=smtp` has no provider reconcile, so a live SMTP retry cannot prove the first send. The default sandbox path reconciles an ambiguous send before retry. That path is `test_ambiguous_outbound_is_reconciled_without_a_second_send`.
- The Playwright demo was not re-run in this audit pass. The earlier result remains `1 passed (8.5s)`.

Browser demo, API already on port 8000, from `apps/web`:

```bash
npx playwright test --reporter=line
```

Result: `1 passed (8.5s)`. The script signed in as Maya, sent the 100-computer request, saw sandbox RFQs, simulated replies, checked a landed cost under $200,000 with readiness 100 of 100, disrupted RAM by 50, approved the new plan by mock iMessage text, saw purchase orders, acknowledged them, and opened the audit entry `orders_confirmed`.

Docker Compose, from `/workspace`. `sudo` was required for the Docker socket. `-d` was used so the process exits while the stack stays up.

The first `sudo docker compose up --build -d` exited 0 and built the images (API/worker pip install, Next.js `npm run build` compiled, containers created). `GET http://127.0.0.1:8000/health` then failed with `curl: (56) Recv failure: Connection reset by peer`. The API container could not open TCP to `db:5432` (timeout). This host had `net.bridge.bridge-nf-call-iptables=1` and an iptables-legacy `FORWARD` policy of `DROP` that did not accept the Compose bridge, so same-bridge traffic was dropped. Host-published port 3000 still returned the dashboard. No Compose file change was required.

Host commands that restored container-to-container TCP:

```bash
sudo sysctl -w net.bridge.bridge-nf-call-iptables=0
sudo sysctl -w net.bridge.bridge-nf-call-ip6tables=0
sudo iptables-legacy -P FORWARD ACCEPT
```

Clean startup after that:

```bash
sudo docker compose down
sudo docker compose up --build -d
```

`docker compose down` exit 0. `sudo docker compose up --build -d` exit 0. All four containers were up (db healthy). Checks immediately after that command:

- `GET http://127.0.0.1:8000/health` → 200 `{"status":"ok"}`
- `GET http://127.0.0.1:8000/ready` → 200 `{"status":"ready"}`
- `GET http://127.0.0.1:8000/api/meta` → 200, banner `Mock mode — no live iMessage or email delivery. Supplier mail stays in the sandbox.`, `live_imessage` false, `live_email` false
- `GET http://127.0.0.1:3000/` → 200, title `Acme procurement`, page includes `Mock mode` and `login-phone`
- `POST /api/auth/login` `{"phone_e164":"+15555550100"}` → 200, Maya Chen, Acme Computing; `GET /api/tasks` → one task, `ACME-1001`, `AWAITING_QUOTES`
- worker container `status=running`, `ExitCode=0`

## Risks

- A restarted worker resumes RFQ delivery and leaves pending approvals untouched. It does not talk to a real supplier.
- The deterministic parser handles the seeded sentence and the structured sandbox replies. Free-form supplier prose needs the live LLM adapter, which is unwired without a key.
- On this VM, Compose networking needed the bridge-netfilter change above before the API could reach Postgres. A normal Docker host that already forwards bridge traffic does not need that step.
