import * as React from "react";
import { CellCard } from "./components/CellCard";
import { EvidenceList } from "./components/EvidenceList";
import { FindingCard } from "./components/FindingCard";
import { LineageGraph } from "./components/LineageGraph";
import { ScanResults } from "./components/ScanResults";
import { SourcePanel } from "./components/SourcePanel";
import { clearHighlights, markCells, settingsStore } from "./office/formatting";
import { NotesUnsupportedError, clearNotes, noteSettingsStore, supportsNotes, writeFindingNotes, type NoteFinding, type NoteResult } from "./office/notes";
import { readPrecedents, readSelectedCell, readSelectedRange, readWorkbookId, selectCell, supportsPrecedents } from "./office/selection";
import { createClient, describeError } from "./services/worldstateApi";
import type { Actor, GraphNode, InspectRequest, ScanResult, SourceDetail, Trace, TraceGraph } from "./types";
import { AS_OF_PRESETS, COMPANY_ORG, DEFAULT_ACTOR, REASON_LABEL, formatDate } from "./view";

const client = createClient();

type Inspected = { request: InspectRequest; precedentsSupported: boolean };

function describeNotes(result: NoteResult): string {
  const parts = [];
  if (result.written) parts.push(`added ${result.written} note(s)`);
  if (result.updated) parts.push(`updated ${result.updated}`);
  if (result.skipped.length) parts.push(`left ${result.skipped.length} existing note(s) alone (${result.skipped.map((key) => key.split("!")[1]).join(", ")})`);
  return parts.length ? ` Notes: ${parts.join(", ")}. Hover a flagged cell to read it.` : "";
}

export function App() {
  const [asOf, setAsOf] = React.useState(AS_OF_PRESETS[0].value);
  const [actors, setActors] = React.useState<Actor[]>([]);
  const [actorId, setActorId] = React.useState(DEFAULT_ACTOR);
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
    client
      .actors()
      .then((all) => setActors(all.filter((actor) => actor.organization_id === COMPANY_ORG)))
      .catch(() => setActors([]));
  }, []);

  const actor = actors.find((item) => item.id === actorId);
  const noteContext = () => ({
    asOfLabel: AS_OF_PRESETS.find((preset) => preset.value === asOf)?.label ?? formatDate(asOf),
    actorLabel: actor ? `${actor.name} (${actor.role})` : actorId,
  });

  async function addNotes(context: Excel.RequestContext, findings: NoteFinding[]): Promise<string> {
    if (!findings.some((finding) => finding.status !== "supported")) return "";
    if (!supportsNotes()) return ` ${new NotesUnsupportedError().message}`;
    return describeNotes(await writeFindingNotes(context, findings, noteSettingsStore(), noteContext()));
  }

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
        actor_id: actorId,
      };
      setInspected({ request, precedentsSupported: read.precedents.supported });
      await runTrace(request);
    });

  const retrace = (changes: Partial<InspectRequest>) => {
    if (!inspected) return;
    const request = { ...inspected.request, ...changes };
    setInspected({ ...inspected, request });
    void run("Re-checking evidence", () => runTrace(request));
  };

  const changeAsOf = (value: string) => {
    setAsOf(value);
    retrace({ as_of: value });
  };

  const changeActor = (value: string) => {
    setActorId(value);
    retrace({ actor_id: value });
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
          actor_id: actorId,
        });
        const marked = await markCells(
          context,
          result.findings.map((item) => ({ key: item.cell, status: item.status })),
          settingsStore(),
        );
        setScan(result);
        const notes = await addNotes(
          context,
          result.findings.map((item) => ({ key: item.cell, status: item.status, title: item.title, summary: item.summary, difference: item.difference })),
        );
        setNotice((marked ? `Highlighted ${marked} cell(s). Values were not changed.` : "Nothing to highlight.") + notes);
      });
    });

  const clear = () =>
    run("Clearing", async () => {
      const [restored, removed] = await Excel.run(async (context) => [
        await clearHighlights(context, settingsStore()),
        await clearNotes(context, noteSettingsStore()),
      ]);
      const parts = [restored ? `restored the original fill on ${restored} cell(s)` : "", removed ? `removed ${removed} WorldState note(s)` : ""].filter(Boolean);
      setNotice(parts.length ? `Cleared: ${parts.join(" and ")}.` : "No WorldState highlights or notes to clear.");
    });

  const noteCurrent = () =>
    run("Adding note", async () => {
      if (!trace) return;
      const finding = { key: trace.cell.key, status: trace.status, title: trace.title, summary: trace.summary, difference: trace.difference };
      const notes = await Excel.run(async (context) => {
        await markCells(context, [{ key: finding.key, status: finding.status }], settingsStore());
        return addNotes(context, [finding]);
      });
      setNotice(notes ? notes.trim() : "This cell is supported, so it gets no note.");
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

      <div className="asof" role="group" aria-label="Viewing as">
        <span className="muted">Viewing as</span>
        <select aria-label="Actor" value={actorId} onChange={(event) => changeActor(event.target.value)} disabled={!!busy || !actors.length}>
          {actors.length ? null : <option value={actorId}>{actorId}</option>}
          {actors.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} ({item.role})
            </option>
          ))}
        </select>
      </div>

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
      {backend === "down" && !error ? <p className="error">WorldState API is not reachable. Start the API and reload this pane.</p> : null}

      {!trace && !scan ? <p className="muted intro">Select a cell, then click Inspect. Select a table, then click Scan range.</p> : null}

      {trace && inspected ? (
        <>
          <FindingCard trace={trace} onAddNote={trace.status === "supported" ? undefined : noteCurrent} busy={!!busy} />
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
          Clear WorldState highlights and notes
        </button>
      </footer>
    </main>
  );
}
