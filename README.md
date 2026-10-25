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

`apps/excel-addin` is a WorldState task pane for Excel. It uses Office.js with React and TypeScript, and is sideloaded rather than published. Its S2c layout presents the selected cell, the backend's finding, a compact formula view, a ring graph, source details, and a timeline of dates returned by the trace API. The pane adapts the existing inspect, scan, graph, and source responses; the API response contracts are unchanged.

The actor and **as of** controls run each trace against a sealed snapshot. The idle view lists flagged cells from a bounded scan of the active sheet. Select a cell and click **Inspect** to open its trace. Click an available source in the graph detail to open the source panel. Excluded sources remain generic and do not expose their amount, date, id, or text in the sidebar.

The bounded scan of the active sheet highlights cells marked for review. **Mark reviewed** clears that cell's WorldState highlight. **Add a note** opens a composer for a custom native Excel note on the inspected cell; a note someone else wrote is left alone, and older Excel builds report when Notes are unavailable. These review actions are local to the workbook and are not saved to the backend.

The add-in never writes values or formulas.

The workbook is generated from the same Decagon ledger rows the API registers:

```bash
pip install openpyxl
PYTHONPATH=services/api python fixtures/build_workbook.py   # writes fixtures/Decagon_Accruals.xlsx
```

| Column | Content |
| --- | --- |
| A | Case |
| B | Vendor |
| C | Accrual ID |
| D | PO ref |
| E | PO amount |
| F | Accrual |
| G | Variance (`=F-E`) |
| H | Period |

The invoice amount is left out on purpose: a September close workbook cannot contain an invoice that arrived later. The server still knows each row's invoice id, so it can find that invoice, and withhold it at an earlier date.

Run the API on port 8000 (see Run above). It registers the workbook once the company ingest has finished. Then:

```bash
cd apps/excel-addin
npm install
npm run dev-server        # https://localhost:3001, proxies /v1, /api and /health to http://localhost:8000
npm start                 # sideloads manifest.xml into desktop Excel
```

If those ports are taken:
1. Start the API on another port.
2. Run `WORLDSTATE_API_PROXY=http://localhost:<api port> npx webpack serve --mode development --port <pane port>`.
3. Sideload a copy of `manifest.xml` with `localhost:3001` replaced by the pane port.

For Excel on the web:
1. Upload `Decagon_Accruals.xlsx` to OneDrive or SharePoint.
2. Run `npm run start -- web --document <workbook URL>`, or use **Insert → Add-ins → Upload My Add-in** with `manifest.xml`.

The first `npm run dev-server` installs a local development certificate (`office-addin-dev-certs`). Stop sideloading with `npm run stop`.

Authentication is in demo mode. The pane sends no credentials. The API traces as `actor-decagon-finance` unless the request names another `actor_id`. Organization, availability, and role checks still run on the server for every trace and every source read.

Cell endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/traces/inspect` | Cell context plus `as_of` and `actor_id` → status, origin, lineage, evidence, unknowns |
| `POST /v1/workbooks/scan` | Up to 200 cells → per-cell findings |
| `GET /v1/traces/{trace_id}/graph` | Nodes and typed edges for the trace |
| `GET /v1/sources/{source_id}?trace_id=` | Source text and its ledger rows. Only works if the source is in that trace's snapshot. Otherwise 403. |

- Errors return `{code, message, request_id}`.
- Until the company ingest finishes, the cell endpoints return `503 INDEX_NOT_READY`.
- `fixtures/truth.json` lists the expected flags per role and date, and why. `services/api/tests/test_workbook.py` checks the scan against it.

Tests:
- `cd apps/excel-addin && npm test`
- `cd services/api && python -m pytest tests/test_workbook.py`

Reset the API by deleting `services/api/worldstate.db` and restarting it. Review highlights are cleared per cell with **Mark reviewed**; custom notes can be removed from Excel's note menu.

## Document ingestion

Open http://localhost:3000/ingest. Upload text, CSV, JSON, email, or PDF files, or run the Cloud Services sample. The run reads each document, finds every printed transaction, records it as evidence, and writes a Graphiti episode. Pause, stop, and resume keep the finished steps. A failed step is the step that runs again.

Set `GRAPHITI_URI` to a Neo4j bolt URL and install `graphiti-core` when a transaction step should call [Graphiti](https://github.com/getzep/graphiti) `add_episode`. Without that connection, the same episode is stored in WorldState and can be sent to Graphiti on a later resume once the URI is set. A received date inside a document controls availability. The date printed on the document does not.

`services/imessage` is the existing Spectrum and Deep Agents channel. It is not the historical evidence gateway and it is not part of the offline demo. The investigator that enforces time and access is the API.
