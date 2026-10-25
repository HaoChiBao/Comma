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

The tests cover temporal filtering, version selection, role grants, cross-organization denial, search and graph leakage, the blocked future invoice, approval authority, the unsafe baseline, and the Excel cell traces.

## Demo

1. Open case `CASE-001` as Maya Chen, accountant, on 2025-09-30, audit mode.
2. The graph shows the $145 purchase order, the $160 email, and the $150 accrual. It does not show the $158 invoice.
3. Run the agent. The attempt to read `INV-381` is denied and logged. The finding cites only visible amounts and proposes human review.
4. Move the date to 2025-12-15 as Jordan Hale, auditor. The invoice enters the snapshot.
5. Open Before / after. The unrestricted column is labeled unsafe and mentions $158. The WorldState column does not.
6. Approve or reject the proposal as the auditor. An accountant cannot approve.

The walkthrough is in `docs/DEMO_SCRIPT.md`. Limits are in `docs/ARCHITECTURE.md`.

## Excel add-in

`apps/excel-addin` is a WorldState task pane for Excel. It uses Office.js with React and TypeScript, and it is sideloaded rather than published. Select a cell and click **Inspect**. The pane shows:

- the value and formula
- the direct precedents (from Office.js `getDirectPrecedents`, ExcelApi 1.12)
- the linked source documents
- a Review / Unknown / Supported finding

The as-of selector re-runs the trace against a snapshot. A source that arrived later comes back as "withheld", with no content or amount. **Scan range** highlights flagged cells. **Clear WorldState highlights** restores the original fills. The add-in never writes values or formulas.

The demo workbook is generated from the same rows the API seeds:

```bash
pip install openpyxl
PYTHONPATH=services/api python fixtures/build_workbook.py   # writes fixtures/WorldState_Demo.xlsx
```

Run the API on port 8000 (see Run above), then:

```bash
cd apps/excel-addin
npm install
npm run dev-server        # https://localhost:3001, proxies /v1 and /health to http://localhost:8000
npm start                 # sideloads manifest.xml into desktop Excel
```

For Excel on the web:
1. Upload `WorldState_Demo.xlsx` to OneDrive or SharePoint.
2. Run `npm run start -- web --document <workbook URL>`, or use **Insert → Add-ins → Upload My Add-in** with `manifest.xml`.

The first `npm run dev-server` installs a local development certificate (`office-addin-dev-certs`). Stop sideloading with `npm run stop`.

Authentication is in demo mode. The pane sends no credentials. The API traces as the seeded accountant, `actor-maya`, unless the request names another `actor_id`. Organization, availability, and role checks still run on the server for every trace and every source read.

Cell endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/traces/inspect` | Cell context plus `as_of` → status, lineage, evidence, unknowns |
| `POST /v1/workbooks/scan` | Up to 200 cells → per-cell findings |
| `GET /v1/traces/{trace_id}/graph` | Nodes and typed edges for the trace |
| `GET /v1/sources/{source_id}?trace_id=` | Source text. Only works if the source is in that trace's snapshot. Otherwise 403. |

Errors return `{code, message, request_id}`. `fixtures/truth.json` lists the planted flags and controls. `services/api/tests/test_workbook.py` checks the scan against it.

Tests:
- `cd apps/excel-addin && npm test`
- `cd services/api && python -m pytest tests/test_workbook.py`

Reset: delete `services/api/worldstate.db` and restart the API. The workbook case `CASE-011` is also added to an existing database on startup. In Excel, click **Clear WorldState highlights**.

## Document ingestion

Open http://localhost:3000/ingest. Upload text, CSV, JSON, email, or PDF files, or run the Cloud Services sample. The run reads each document, finds every printed transaction, records it as evidence, and writes a Graphiti episode. Pause, stop, and resume keep the finished steps. A failed step is the step that runs again.

Set `GRAPHITI_URI` to a Neo4j bolt URL and install `graphiti-core` when a transaction step should call [Graphiti](https://github.com/getzep/graphiti) `add_episode`. Without that connection, the same episode is stored in WorldState and can be sent to Graphiti on a later resume once the URI is set. A received date inside a document controls availability. The date printed on the document does not.

`services/imessage` is the existing Spectrum and Deep Agents channel. It is not the historical evidence gateway and it is not part of the offline demo. The investigator that enforces time and access is the API.
