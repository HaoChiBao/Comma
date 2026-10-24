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
- `docker compose up --build` was not executed in this environment. `docker` is not installed. Migrations, seed, the API, pytest, and the browser demo were run against local PostgreSQL 16.

P2 (voice, negotiation, supplier discovery, policy learning, CRM, ERP) is out of scope.

## Verification

Backend, from `services/api`, against `procurement_test`:

```bash
DATABASE_URL=postgresql+psycopg://acme:acme@localhost:5432/procurement_test PYTHONPATH=. python -m pytest --tb=no --junitxml=/tmp/pytest-junit.xml -q
```

JUnit attributes from that run: `tests=37` `failures=0` `errors=0` `skipped=0` `time=19.319`. Exit code 0.

Breakdown: `test_acceptance.py` 16, `test_extract_and_policy.py` 9, `test_optimizer.py` 7, `test_stress.py` 5.

Browser demo, API already on port 8000, from `apps/web`:

```bash
npx playwright test --reporter=line
```

Result: `1 passed (8.5s)`. The script signed in as Maya, sent the 100-computer request, saw sandbox RFQs, simulated replies, checked a landed cost under $200,000 with readiness 100 of 100, disrupted RAM by 50, approved the new plan by mock iMessage text, saw purchase orders, acknowledged them, and opened the audit entry `orders_confirmed`.

`docker compose up --build`: not run. Command `docker` was not found.

## Risks

- A restarted worker resumes RFQ delivery and leaves pending approvals untouched. It does not talk to a real supplier.
- The deterministic parser handles the seeded sentence and the structured sandbox replies. Free-form supplier prose needs the live LLM adapter, which is unwired without a key.
- Compose file paths and the local migrate/seed/uvicorn path match, but the image build itself is unverified here.
