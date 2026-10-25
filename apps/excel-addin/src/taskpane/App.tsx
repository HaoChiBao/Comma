import * as React from "react";
import { AsOfMenu } from "./components/AsOfMenu";
import { IdleView } from "./components/IdleView";
import { LoadingView } from "./components/LoadingView";
import { ResultView } from "./components/ResultView";
import { SourcePanel } from "./components/SourcePanel";
import { clearHighlight, markCells, settingsStore } from "./office/formatting";
import { noteSettingsStore, writeCustomNote } from "./office/notes";
import { reviewKey, reviewStateStore } from "./office/reviewed";
import { readPrecedents, readSelectedCell, readSelection, readUsedRange, readWorkbookId, selectCell, supportsPrecedents, watchSelection, type RangeCells, type SelectionSummary } from "./office/selection";
import { createClient, describeError } from "./services/worldstateApi";
import type { Actor, AvailableEvidence, InspectRequest, ScanFinding, SourceDetail, Trace, TraceGraph } from "./types";
import { COMPANY_ORG, DEFAULT_ACTOR, REASON_LABEL, formatDate, formatMoney } from "./view";
import { AS_OF_PRESETS, LOADING_MS, asOfPreset, defaultSelection, loadingDone, loadingStep } from "./sidebarView";
import { toSidebarTrace } from "./sidebarPresentation";

const client = createClient();
type Phase = "idle" | "loading" | "done";
type DisplayFinding = ScanFinding & { vendor_name?: string };
const VENDOR_EMAIL_FALLBACK = "nathan.wan23@gmail.com";

function vendorEmail(trace: Trace): string {
  const emails = trace.evidence.filter((item): item is AvailableEvidence => item.status === "available" && item.kind === "email");
  for (const item of emails) {
    const from = /^From:\s*([^\r\n]+)/im.exec(item.snippet)?.[1];
    const address = from?.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0];
    if (address && !/\.(example|test|invalid|localhost)$/i.test(address.split("@")[1])) return address;
  }
  return VENDOR_EMAIL_FALLBACK;
}

function vendorEmailDraft(trace: Trace): { recipient: string; subject: string; body: string } {
  const vendor = trace.cell.vendor_name || "the vendor";
  const amount = typeof trace.cell.raw_value === "number"
    ? formatMoney(trace.cell.raw_value)
    : trace.cell.display_text || String(trace.cell.raw_value ?? "—");
  const evidence = trace.evidence
    .filter((item): item is AvailableEvidence => item.status === "available" && item.cell === trace.cell.key)
    .map((item) => {
      const amountText = item.amount ? `, ${formatMoney(item.amount, item.currency)}` : "";
      const receivedText = item.available_at ? `, received ${formatDate(item.available_at)}` : "";
      return `- ${item.external_id} (${item.kind}${amountText}${receivedText})`;
    });
  const period = trace.cell.period ? `\nPeriod: ${trace.cell.period}` : "";
  const difference = trace.difference ? `\nDifference: ${formatMoney(trace.difference.amount, trace.difference.currency)}` : "";
  const evidenceText = evidence.length ? evidence.join("\n") : "- No supporting documents were available in this view.";
  const subject = `Question about ${vendor} — ${trace.cell.address} (${amount})`;
  const body = [
    `Hello ${vendor},`,
    "",
    "Could you help us confirm the amount recorded for this item?",
    `Workbook cell: ${trace.cell.key}`,
    `Recorded amount: ${amount}${period}`,
    `Formula: ${trace.cell.formula || "—"}`,
    `Finding: ${trace.title}`,
    `Details: ${trace.summary}${difference}`,
    `Snapshot date: ${formatDate(trace.as_of)}`,
    "",
    "Available evidence:",
    evidenceText,
    "",
    "Could you confirm the correct amount and applicable period?",
  ].join("\n");
  return { recipient: vendorEmail(trace), subject, body };
}

function withVendor(findings: ScanFinding[], cells: RangeCells["cells"]): DisplayFinding[] {
  const values = new Map(cells.map((cell) => [cell.address, cell.raw_value]));
  return findings.map((finding) => {
    const row = /([0-9]+)$/.exec(finding.address)?.[1];
    const vendor = row ? values.get(`B${row}`) : undefined;
    return { ...finding, vendor_name: typeof vendor === "string" ? vendor : undefined };
  });
}

export function App() {
  const [asOf, setAsOf] = React.useState(AS_OF_PRESETS[0].value);
  const [actors, setActors] = React.useState<Actor[]>([]);
  const [actorId, setActorId] = React.useState(DEFAULT_ACTOR);
  const [backend, setBackend] = React.useState<"checking" | "ready" | "down">("checking");
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [step, setStep] = React.useState(0);
  const [selection, setSelection] = React.useState<SelectionSummary | null>(null);
  const [trace, setTrace] = React.useState<Trace | null>(null);
  const [graph, setGraph] = React.useState<TraceGraph | null>(null);
  const [request, setRequest] = React.useState<InspectRequest | null>(null);
  const [traceVendor, setTraceVendor] = React.useState("");
  const [selectedItem, setSelectedItem] = React.useState("");
  const [flagged, setFlagged] = React.useState<DisplayFinding[] | null>(null);
  const [scanNote, setScanNote] = React.useState<string | null>(null);
  const [source, setSource] = React.useState<SourceDetail | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const noticeTimer = React.useRef<number | null>(null);

  const token = React.useRef(0);
  const phaseRef = React.useRef<Phase>("idle");
  const traceRef = React.useRef<Trace | null>(null);
  const requestRef = React.useRef<InspectRequest | null>(null);
  const filtersRef = React.useRef({ asOf, actorId });
  const initialReviewHighlightsDone = React.useRef(false);
  const initialReviewHighlightsPending = React.useRef(false);
  phaseRef.current = phase;
  traceRef.current = trace;
  requestRef.current = request;
  filtersRef.current = { asOf, actorId };
  const preset = asOfPreset(asOf);

  function clearNoticeTimer() {
    if (noticeTimer.current !== null) {
      window.clearTimeout(noticeTimer.current);
      noticeTimer.current = null;
    }
  }

  function dismissNotice() {
    clearNoticeTimer();
    setNotice(null);
  }

  function showNotice(message: string) {
    clearNoticeTimer();
    setNotice(message);
    noticeTimer.current = window.setTimeout(() => {
      setNotice(null);
      noticeTimer.current = null;
    }, 4000);
  }

  React.useEffect(() => () => clearNoticeTimer(), []);

  const refreshFlags = React.useCallback(async (date: string, actor: string) => {
    try {
      const reviewed = new Set(reviewStateStore().load());
      const result = await Excel.run(async (context) => {
        const workbookId = await readWorkbookId(context);
        const range = await readUsedRange(context);
        if (!range) return { result: null, range: null };
        const scan = await client.scan({ workbook_id: workbookId, sheet_name: range.sheetName, range_address: range.rangeAddress, cells: range.cells, as_of: date, actor_id: actor });
        if (!initialReviewHighlightsDone.current && !initialReviewHighlightsPending.current) {
          initialReviewHighlightsPending.current = true;
          try {
            const reviewMarks = scan.findings
              .filter((finding) => finding.status === "review" && !reviewed.has(reviewKey(finding.cell, date, actor)))
              .map((finding) => ({ key: finding.cell, status: finding.status }));
            await markCells(context, reviewMarks, settingsStore());
            initialReviewHighlightsDone.current = true;
          } finally {
            initialReviewHighlightsPending.current = false;
          }
        }
        return { result: scan, range };
      });
      if (result.result && result.range) {
        setFlagged(withVendor(result.result.findings, result.range.cells)
          .filter((finding) => !reviewed.has(reviewKey(finding.cell, date, actor))));
        setScanNote(null);
        setBackend("ready");
      } else {
        setFlagged([]);
      }
    } catch (err) {
      setFlagged(null);
      setScanNote(describeError(err));
      if (/not reachable/i.test(describeError(err))) setBackend("down");
    }
  }, []);

  React.useEffect(() => {
    client.health().then(() => setBackend("ready")).catch(() => setBackend("down"));
    client.actors().then((all) => setActors(all.filter((item) => item.organization_id === COMPANY_ORG))).catch(() => setActors([]));
    void refreshFlags(asOf, actorId);
    const refreshSelection = async () => {
      try {
        const next = await Excel.run((context) => readSelection(context));
        setSelection(next);
        const current = traceRef.current;
        const key = next.single ? next.cell.key : null;
        if (phaseRef.current === "loading" || (phaseRef.current === "done" && current && key !== current.cell.key)) {
          token.current += 1;
          setPhase("idle");
          setTrace(null);
          setGraph(null);
          setSource(null);
        }
      } catch {
        // Excel can reject a read briefly while a cell is being edited; the next selection event retries it.
      }
    };
    void refreshSelection();
    let stop: (() => Promise<void>) | null = null;
    watchSelection(() => {
      void refreshSelection();
      void refreshFlags(filtersRef.current.asOf, filtersRef.current.actorId);
    }).then((remove) => { stop = remove; }).catch(() => undefined);
    return () => { if (stop) void stop(); };
    // The event listener is registered once; explicit handlers pass current filter values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(label: string, task: () => Promise<void>): Promise<boolean> {
    setBusy(label);
    setError(null);
    dismissNotice();
    try {
      await task();
      return true;
    } catch (err) {
      setError(describeError(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function inspectWith(requestBody: InspectRequest, vendor = "") {
    const mine = ++token.current;
    setPhase("loading");
    setStep(0);
    setError(null);
    const started = performance.now();
    let real: 0 | 1 | 2 = 0;
    const timer = window.setInterval(() => { if (token.current === mine) setStep(loadingStep(performance.now() - started, real)); }, 60);
    try {
      const result = await client.inspect(requestBody);
      real = 1;
      const graphResult = await client.graph(result.trace_id);
      real = 2;
      while (!loadingDone(performance.now() - started, real)) {
        await new Promise((resolve) => setTimeout(resolve, Math.max(16, LOADING_MS - (performance.now() - started))));
      }
      if (token.current !== mine) return;
      setRequest(requestBody);
      setTraceVendor(vendor);
      setTrace(result);
      setGraph(graphResult);
      setSelectedItem(defaultSelection(toSidebarTrace(result, graphResult, requestBody)));
      setSource(null);
      setPhase("done");
      setBackend("ready");
    } catch (err) {
      if (token.current === mine) {
        setError(describeError(err));
        setPhase("idle");
      }
    } finally {
      window.clearInterval(timer);
    }
  }

  const inspect = () => run("Inspecting", async () => {
    const read = await Excel.run(async (context) => {
      const workbookId = await readWorkbookId(context);
      const cell = await readSelectedCell(context);
      const precedents = await readPrecedents(context, cell, supportsPrecedents());
      const summary = await readSelection(context);
      return { workbookId, cell, precedents, vendor: summary.single ? summary.vendor : "" };
    });
    await inspectWith({
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
    }, read.vendor);
  });

  const retrace = (changes: Partial<InspectRequest>, newAsOf = asOf, newActor = actorId) => {
    const previous = requestRef.current;
    if (!previous) return;
    void run("Re-checking evidence", () => inspectWith({ ...previous, ...changes, as_of: newAsOf, actor_id: newActor }, traceVendor));
  };

  const changeAsOf = (value: string) => {
    setAsOf(value);
    void refreshFlags(value, actorId);
    retrace({ as_of: value }, value, actorId);
  };

  const changeActor = (value: string) => {
    setActorId(value);
    void refreshFlags(asOf, value);
    retrace({ actor_id: value }, asOf, value);
  };

  const noteCurrent = async (message: string): Promise<boolean> => {
    if (!trace) return false;
    return run("Adding note", async () => {
      const result = await Excel.run((context) => writeCustomNote(context, trace.cell.key, message, noteSettingsStore()));
      showNotice(result === "skipped"
        ? "This cell already has a note that Comma did not create; it was left unchanged."
        : `${result === "written" ? "Added" : "Updated"} note on ${trace.cell.address}.`);
    });
  };

  const goToCell = (key: string) => run("Selecting", () => Excel.run((context) => selectCell(context, key)));
  const openSource = (sourceId: string) => run("Opening source", async () => {
    if (trace) setSource(await client.source(sourceId, trace.trace_id));
  });
  const act = (label: string) => {
    if (label === "Ask vendor" && trace) {
      const draft = vendorEmailDraft(trace);
      const mailto = `mailto:${encodeURIComponent(draft.recipient)}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
      const link = document.createElement("a");
      link.href = mailto;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      showNotice(draft.recipient === VENDOR_EMAIL_FALLBACK
        ? `Email draft opened to ${draft.recipient}; no vendor email was available.`
        : `Email draft opened to ${draft.recipient}.`);
      return;
    }
    if (label !== "Mark reviewed" || !trace) {
      showNotice(`${label} · not saved yet`);
      return;
    }
    void run(label, async () => {
      await Excel.run((context) => clearHighlight(context, trace.cell.key, settingsStore()));
      const reviewed = reviewStateStore();
      await reviewed.save([...reviewed.load(), reviewKey(trace.cell.key, asOf, actorId)]);
      await refreshFlags(asOf, actorId);
      showNotice("Marked reviewed. This decision is saved in this workbook only.");
    });
  };

  const selectionCell = selection?.single ? selection.cell : null;
  const sidebarTrace = trace && graph && request ? toSidebarTrace(trace, graph, request) : null;

  return (
    <div className="ws">
      <header className="top">
        <img className="brand-logo" src="assets/comma-logo.png" alt="Comma" />
        <div className="top-controls">
          <label className="actor-select"><span className="sr-only">Viewing as</span>
            <select aria-label="Actor" value={actorId} onChange={(event) => changeActor(event.target.value)} disabled={!!busy || !actors.length}>
              {actors.length ? null : <option value={actorId}>{actorId}</option>}
              {actors.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.role})</option>)}
            </select>
          </label>
          <AsOfMenu value={asOf} onChange={changeAsOf} />
        </div>
      </header>

      <div className="scroll">
        {busy ? <p className="status">{busy}…</p> : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        {notice ? <p className="notice" role="status" aria-live="polite">{notice}</p> : null}
        {backend === "down" && !error ? <p className="inline-error" role="alert">Comma is not reachable. Your workbook was not changed.</p> : null}

        {phase === "idle" ? <IdleView
          selection={selection}
          asOfShort={preset.short}
          flagged={flagged}
          scanNote={scanNote}
          error={null}
          onInspect={() => void inspect()}
          onPick={(key) => void goToCell(key)}
        /> : null}
        {phase === "loading" ? <LoadingView cellKey={selectionCell?.key ?? ""} address={selectionCell?.address ?? ""} asOfShort={preset.short} step={step} /> : null}
        {phase === "done" && sidebarTrace ? <ResultView
          trace={sidebarTrace}
          vendor={traceVendor}
          selected={selectedItem}
          onSelect={setSelectedItem}
          onOpenSource={openSource}
          onAddNote={noteCurrent}
          onAction={act}
          busy={!!busy}
        /> : null}

        {source ? <SourcePanel source={source} onClose={() => setSource(null)} /> : null}
      </div>
    </div>
  );
}
