# WorldState

A time machine for an accrual investigation. The agent may use only the evidence that was available to a role at a chosen moment. A later invoice, even one dated in September, stays out of a September snapshot until accounting actually received it.

The offline demo needs no model key. It does not post accounting entries.

## Run

Postgres, the API, and the dashboard:

```bash
docker compose up --build
```

Open http://localhost:3000. The API is http://localhost:8000/health.

The hosted dashboard is https://web-production-def1f.up.railway.app and the hosted API is https://api-production-69e9.up.railway.app. Postgres tables for this product live in the `worldstate` schema so they do not collide with the earlier procurement tables.

Without Docker, from `services/api`:

```bash
pip install -r requirements.txt
set PYTHONPATH=.
python -m uvicorn app.main:app --port 8000
```

On macOS or Linux, use `export PYTHONPATH=.` instead of `set`. This path uses a local SQLite file, `worldstate.db`. Then, from `apps/web`:

```bash
npm install
npm run dev
```

## Tests

```bash
cd services/api
python -m pytest
```

The tests cover temporal filtering, version selection, role grants, cross-organization denial, search and graph leakage, the blocked future invoice, approval authority, and the unsafe baseline.

## Demo

1. Open case `CASE-001` as Maya Chen, accountant, on 2025-09-30, audit mode.
2. The graph shows the $145 purchase order, the $160 email, and the $150 accrual. It does not show the $158 invoice.
3. Run the agent. The attempt to read `INV-381` is denied and logged. The finding cites only visible amounts and proposes human review.
4. Move the date to 2025-12-15 as Jordan Hale, auditor. The invoice enters the snapshot.
5. Open Before / after. The unrestricted column is labeled unsafe and mentions $158. The WorldState column does not.
6. Approve or reject the proposal as the auditor. An accountant cannot approve.

The walkthrough is in `docs/DEMO_SCRIPT.md`. Limits are in `docs/ARCHITECTURE.md`.

## Document ingestion

Open http://localhost:3000/ingest. Upload text, CSV, JSON, email, or PDF files, or run the Cloud Services sample. The run reads each document, finds every printed transaction, records it as evidence, and writes a Graphiti episode. Pause, stop, and resume keep the finished steps. A failed step is the step that runs again.

Set `GRAPHITI_URI` to a Neo4j bolt URL and install `graphiti-core` when a transaction step should call [Graphiti](https://github.com/getzep/graphiti) `add_episode`. Without that connection, the same episode is stored in WorldState and can be sent to Graphiti on a later resume once the URI is set. A received date inside a document controls availability. The date printed on the document does not.

`services/imessage` is the existing Spectrum and Deep Agents channel. It is not the historical evidence gateway and it is not part of the offline demo. The investigator that enforces time and access is the API.
