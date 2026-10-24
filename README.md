# Acme Computing procurement agent

P0 vertical slice for a multi-tenant procurement agent. A manager texts a desktop-computer request. The agent builds an 8-component bill of materials, asks approved suppliers for quotes, computes a landed-cost plan with `Decimal` money, and stops at a human approval. Only the approval gateway can create purchase orders. Every transition is written to an append-only audit log.

The procurement API and supplier email ship in **mock mode**. Live iMessage runs separately in `services/imessage`, connected to the Photon project `calhacks`. That process does not send email.

## Mock mode and live integrations

| Integration | What this build does | What it does not do |
| --- | --- | --- |
| Photon / Spectrum iMessage | The API still verifies Stable webhook signatures (`v0:{timestamp}:{rawBody}`, `X-Spectrum-Signature`) and writes outbound text to the sandbox ledger when `PHOTON_MODE=mock`. Live iMessage is `services/imessage`: `spectrum-ts` with the cloud iMessage provider, `space.send`, and a local `.env` (`PROJECT_ID`, `PROJECT_SECRET`). | `PHOTON_MODE=live` on the API still fails closed. The API process does not open a Spectrum session. Do not commit `services/imessage/.env`. |
| Supplier email | `EMAIL_MODE=sandbox` stores RFQs and purchase orders in the same ledger. Inbound mail is a local webhook: `X-Email-Signature: sha256=` plus HMAC-SHA256 of the raw body using `EMAIL_WEBHOOK_SECRET`. Correlation uses `RFQ-TOKEN` in the body. The subject is ignored. | This is not Gmail, not a provider mailbox, and not SMTP. `EMAIL_MODE=smtp` exists and is unused unless a host and username are set. The default sandbox never opens a socket. |
| LLM | `app/adapters/llm.py` calls a chat-completions endpoint only when `LLM_MODE=live` and `LLM_API_KEY` is set. | No key is configured. Intake and quote extraction use the deterministic parser. Tests and the demo do not call a model. |

The dashboard banner states the same boundary: mock mode, no live iMessage or email delivery.

## Setup

Postgres 16, Python 3.12, and Node 22 are enough for a local run. Docker Compose is the single-command path when Docker is available:

```bash
docker compose up --build
```

That starts Postgres, runs migrations, seeds Acme Computing and Beta Industrial, starts the API on port 8000, starts the worker, and serves the console on port 3000.

Without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r services/api/requirements.txt
createdb procurement   # role/password acme/acme, or edit DATABASE_URL
cd services/api && PYTHONPATH=. python -m app.seed --reset
PYTHONPATH=. uvicorn app.main:app --port 8000
```

In another shell:

```bash
cd apps/web && npm install && npm run dev
```

Copy `.env.example` if you want overrides. Leave `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `SMTP_*`, and `LLM_API_KEY` empty.

Reset fixtures, including the seeded demo sentence:

```bash
scripts/demo_reset
```

The business clock is anchored at `DEMO_CLOCK` (2026-10-24 15:00 UTC) so the November 15 deadline stays coherent. Leases still move with elapsed time unless a test freezes the clock. Spectrum signature checks use wall-clock time, not the demo clock.

## Architecture

- `apps/web` — Next.js console. Task list, composer, BOM tree, plan, sandbox inbox, audit. It polls the API.
- `services/api` — FastAPI, SQLAlchemy 2, PostgreSQL. Money is `NUMERIC(14,2)` and Python `Decimal`. API money fields are strings.
- `app/orchestrator.py` — deterministic tool loop. It can source, RFQ, plan, and request approval. It cannot approve, issue a purchase order, edit the audit log, or relax a hard constraint.
- `app/optimizer.py` — exact search. Shipping and taxes are charged once per supplier, never once per unit. Unknown shipping or tax excludes the quote. Compatibility rules come from the database.
- `app/gateway.py` — the only purchase-order writer. It locks the approval row, checks role, company, expiry, plan hash, and the actor's limit, and is idempotent on retry.
- `app/worker.py` — DB-backed jobs and a transactional outbox. Claims use `FOR UPDATE SKIP LOCKED`. A restarted worker resumes outstanding RFQs. Pending approvals stay pending until a person acts.
- `app/audit.py` plus a database trigger — `work_events` rejects UPDATE and DELETE.

Tenants are Acme Computing (Maya Chen, manager, `+15555550100`; Jordan Hale, controller; Sam Ortiz, requester) and Beta Industrial (Riley Quinn). Reads and writes filter on the company of the signed-in actor. An unknown phone gets an onboarding reply and no task data.

Demo mutation routes (`/dev/*`) return 404 unless `DEMO_MODE=true` and `APP_ENV` is not `production`.

## Demo

1. Open http://localhost:3000. The banner should say messages are not delivered.
2. Sign in as `+15555550100`.
3. Send the prefilled request for 100 computers, a $200,000 budget, and delivery by November 15. Destination defaults to the Austin ship-to and that assumption is recorded.
4. The task moves to `AWAITING_QUOTES`. Sandbox RFQs include an `RFQ-TOKEN`. Helios's old GPU quote is stale, so it is asked again. Missing components are asked from the other approved suppliers.
5. Click **Simulate supplier replies**. Extraction stays on the deterministic parser. The plan total is under budget, readiness is 100 of 100, and the chosen platform is the compatible LGA build (the cheaper Helios GPU misses November 15).
6. Click **Disrupt RAM −50** before approving. The previous plan and approval are invalidated. Lattice can no longer cover 200 sticks alone, so the agent splits the buy with Summit. GPU stock still covers 100 computers.
7. Fill **Approve via mock iMessage** (or send `APPROVE APV-…` in the reply box). The gateway writes one purchase order per supplier. A second approval returns those same orders.
8. Click **Acknowledge orders**. The task becomes `COMPLETED` with the label `orders confirmed; delivery pending`. The audit explains why the orders were placed.

`scripts/demo_reset` puts the fixtures back.

## Tests

```bash
cd services/api
DATABASE_URL=postgresql+psycopg://acme:acme@localhost:5432/procurement_test PYTHONPATH=. pytest
cd ../../apps/web && npx playwright test
```

Results from this workspace are in `IMPLEMENTATION_STATUS.md`. `sudo docker compose up --build -d` from the repo root exited 0 after a host bridge-netfilter change recorded there. The API answered `/health`, `/ready`, and `/api/meta`, and the dashboard on port 3000 returned the Acme procurement page.
