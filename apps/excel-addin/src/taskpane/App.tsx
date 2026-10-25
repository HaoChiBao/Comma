import * as React from "react";
import { CellCard } from "./components/CellCard";
import { EvidenceList } from "./components/EvidenceList";
import { FindingCard } from "./components/FindingCard";
import { LineageGraph } from "./components/LineageGraph";
import { ScanResults } from "./components/ScanResults";
import { SourcePanel } from "./components/SourcePanel";
import { clearHighlights, markCells, settingsStore } from "./office/formatting";
import { readPrecedents, readSelectedCell, readSelectedRange, readWorkbookId, selectCell, supportsPrecedents } from "./office/selection";
import { createClient, describeError } from "./services/worldstateApi";
import type { GraphNode, InspectRequest, ScanResult, SourceDetail, Trace, TraceGraph } from "./types";
import { AS_OF_PRESETS, REASON_LABEL } from "./view";

const client = createClient();

type Inspected = { request: InspectRequest; precedentsSupported: boolean };

export function App() {
  const [asOf, setAsOf] = React.useState(AS_OF_PRESETS[0].value);
  const [backend, setBackend] = React.useState<"checking" | "ready" | "down">("checking");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [inspected, setInspected] = React.useState<Inspected | null>(null);
  const [trace, setTrace] = React.useState<Trace | null>(null);
  const [showGraph, setShowGraph] = React.useState(false);
  const [graph, setGraph] = React.useState<TraceGraph | null>(null);
  const [source, setSource] = React.useState<SourceDetail | null>(null);
  const [scan, setScan] = React.useState<ScanResult | null>(null);

  React.useEffect(() => {
    client
      .health()
      .then(() => setBackend("ready"))
      .catch(() => setBackend("down"));
  }, []);

  React.useEffect(() => {
    if (!showGraph || !trace) return;
    let live = true;
    client
      .graph(trace.trace_id)
      .then((result) => live && setGraph(result))
      .catch((err) => live && setError(describeError(err)));
    return () => {
      live = false;
    };
  }, [showGraph, trace]);

  async function run(label: string, task: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await task();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function runTrace(request: InspectRequest) {
    const result = await client.inspect(request);
    setTrace(result);
    setGraph(null);
    setSource(null);
    setBackend("ready");
  }

  const inspect = () =>
    run("Inspecting", async () => {
      const read = await Excel.run(async (context) => {
        const workbookId = await readWorkbookId(context);
        const cell = await readSelectedCell(context);
        const supported = supportsPrecedents();
        const precedents = await readPrecedents(context, cell, supported);
        return { workbookId, cell, precedents };
      });
      const request: InspectRequest = {
        workbook_id: read.workbookId,
        sheet_name: read.cell.sheetName,
        cell_address: read.cell.address,
        raw_value: read.cell.rawValue,
        display_text: read.cell.displayText,
        formula: read.cell.formula,
        direct_precedents: read.precedents.addresses,
        precedent_values: read.precedents.values,
        as_of: asOf,
      };
      setInspected({ request, precedentsSupported: read.precedents.supported });
      await runTrace(request);
    });

  const changeAsOf = (value: string) => {
    setAsOf(value);
    if (inspected) {
      const request = { ...inspected.request, as_of: value };
      setInspected({ ...inspected, request });
      void run("Re-checking evidence", () => runTrace(request));
    }
  };

  const scanRange = () =>
    run("Scanning", async () => {
      await Excel.run(async (context) => {
        const workbookId = await readWorkbookId(context);
        const range = await readSelectedRange(context);
        const result = await client.scan({
          workbook_id: workbookId,
          sheet_name: range.sheetName,
          range_address: range.rangeAddress,
          cells: range.cells,
          as_of: asOf,
        });
        const marked = await markCells(
          context,
          result.findings.map((item) => ({ key: item.cell, status: item.status })),
          settingsStore(),
        );
        setScan(result);
        setNotice(marked ? `Highlighted ${marked} cell(s). Values were not changed.` : "Nothing to highlight.");
      });
    });

  const clear = () =>
    run("Clearing", async () => {
      const restored = await Excel.run((context) => clearHighlights(context, settingsStore()));
      setNotice(restored ? `Restored the original fill on ${restored} cell(s).` : "No WorldState highlights to clear.");
    });

  const goToCell = (key: string) => run("Selecting", () => Excel.run((context) => selectCell(context, key)));

  const openSource = (sourceId: string) =>
    run("Opening source", async () => {
      if (!trace) return;
      setSource(await client.source(sourceId, trace.trace_id));
    });

  const selectNode = (node: GraphNode) => {
    if (node.type === "SpreadsheetCell") void goToCell(node.id);
    else if (node.type === "Blocked") setNotice(`${REASON_LABEL[node.reason ?? ""] ?? "Withheld"}. Move the date later to see it if it exists.`);
    else if (node.source_id) void openSource(node.source_id);
  };

  const custom = !AS_OF_PRESETS.some((preset) => preset.value === asOf);

  return (
    <main>
      <header className="top">
        <div className="brand">
          WorldState <span className={`dot ${backend}`} title={`API ${backend}`} />
        </div>
        <div className="actions">
          <button onClick={inspect} disabled={!!busy}>
            Inspect
          </button>
          <button onClick={scanRange} disabled={!!busy}>
            Scan range
          </button>
          <button onClick={() => setShowGraph((value) => !value)} disabled={!trace}>
            {showGraph ? "Hide trace" : "Trace"}
          </button>
        </div>
      </header>

      <div className="asof" role="group" aria-label="Evidence as of">
        <span className="muted">As of</span>
        {AS_OF_PRESETS.map((preset) => (
          <button key={preset.value} className={`chip ${asOf === preset.value ? "on" : ""}`} onClick={() => changeAsOf(preset.value)} disabled={!!busy}>
            {preset.label}
          </button>
        ))}
        <input
          type="date"
          aria-label="Custom as-of date"
          className={custom ? "on" : ""}
          value={custom ? asOf.slice(0, 10) : ""}
          onChange={(event) => event.target.value && changeAsOf(`${event.target.value}T23:59:59`)}
        />
      </div>

      {busy ? <p className="status">{busy}…</p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}
      {backend === "down" && !error ? <p className="error">WorldState API is not reachable. Start it on port 8000.</p> : null}

      {!trace && !scan ? <p className="muted intro">Select a cell, then click Inspect. Select a table, then click Scan range.</p> : null}

      {trace && inspected ? (
        <>
          <FindingCard trace={trace} />
          <CellCard trace={trace} precedentValues={inspected.request.precedent_values} precedentsSupported={inspected.precedentsSupported} onSelectCell={goToCell} />
          {showGraph && graph ? <LineageGraph graph={graph} onSelectNode={selectNode} /> : null}
          {source ? <SourcePanel source={source} onClose={() => setSource(null)} /> : null}
          <EvidenceList trace={trace} onOpenSource={openSource} />
          <p className="muted mono">
            snapshot {trace.snapshot_id} · {trace.manifest_hash.slice(0, 12)}
          </p>
        </>
      ) : null}

      {scan ? <ScanResults result={scan} onSelectCell={goToCell} /> : null}

      <footer>
        <button className="link" onClick={clear} disabled={!!busy}>
          Clear WorldState highlights
        </button>
      </footer>
    </main>
  );
}
