import { describe, expect, it } from "vitest";
import { toSidebarTrace } from "../src/taskpane/sidebarPresentation";
import type { InspectRequest, Trace, TraceGraph } from "../src/taskpane/types";

const request: InspectRequest = {
  workbook_id: "decagon-accruals-2025-09",
  sheet_name: "Accruals",
  cell_address: "F2",
  raw_value: 150,
  display_text: "$150.00",
  formula: "",
  direct_precedents: [],
  precedent_values: {},
  as_of: "2025-09-30T23:59:00+00:00",
  actor_id: "actor-decagon-finance",
};

const trace: Trace = {
  trace_id: "trc_1",
  snapshot_id: "snap_1",
  manifest_hash: "abc123abc123abc123",
  as_of: request.as_of,
  actor_id: request.actor_id,
  case_id: "CASE-001",
  cell: { key: "Accruals!F2", sheet_name: "Accruals", address: "F2", raw_value: 150, display_text: "$150.00", formula: "", registered: true, vendor_name: "Cloud Services Inc.", period: "2025-09", reference: "ACR-C01-202509" },
  status: "review",
  title: "Potential estimate discrepancy",
  summary: "MSG-0001 shows $160.00; this cell is $150.00.",
  difference: { amount: "10.00", currency: "USD" },
  unknowns: [],
  origin: null,
  lineage: [],
  evidence: [
    { cell: "Accruals!F2", source_id: "MSG-0001:v1", status: "available", external_id: "MSG-0001", kind: "email", title: "September estimate", amount: "160.00", currency: "USD", period: "2025-09", document_date: "2025-09-25T00:00:00+00:00", available_at: "2025-09-25T17:00:00+00:00", locator: "paragraph 1", ledger_locator: null, snippet: "Our revised estimate is $160.00.", relationship: "CONFLICTS_WITH", link_status: "candidate", basis: "Email names the PO", comparison: "differs", difference: "10.00" },
    { cell: "Accruals!F2", source_id: "JE-900:v1", status: "available", external_id: "JE-900", kind: "journal", title: "September accrual", amount: "150.00", currency: "USD", period: "2025-09", document_date: "2025-09-30T17:00:00+00:00", available_at: "2025-09-30T17:00:00+00:00", locator: "journal line", ledger_locator: "journal.csv:JE-900", snippet: "Accrual posted at close.", relationship: "RECORDED_AS", link_status: "verified", basis: "The posted accrual entry", comparison: "match", difference: null },
    { cell: "Accruals!F2", source_id: "INV-C01-381:v1", status: "excluded", reason: "FUTURE_INFORMATION", relationship: "POSSIBLY_RELATED_TO", link_status: "candidate" },
  ],
};

const graph: TraceGraph = {
  trace_id: "trc_1",
  as_of: request.as_of,
  nodes: [
    { id: "Accruals!F2", type: "SpreadsheetCell", label: "F2", title: "Accruals!F2", amount: "150.00", status: "review", selected: true },
    { id: "source:MSG-0001:v1", type: "Email", label: "MSG-0001", title: "September estimate", amount: "160.00", source_id: "MSG-0001:v1", available_at: "2025-09-25T17:00:00+00:00" },
    { id: "source:JE-900:v1", type: "Journal", label: "JE-900", title: "September accrual", amount: "150.00", source_id: "JE-900:v1", available_at: "2025-09-30T17:00:00+00:00" },
    { id: "blocked:INV-C01-381:v1", type: "Blocked", label: "Withheld", title: "Not available at this date", amount: null, reason: "FUTURE_INFORMATION", source_id: null },
  ],
  edges: [
    { id: "e1", source: "Accruals!F2", target: "source:MSG-0001:v1", type: "CONFLICTS_WITH", verified: false, inferred: true },
    { id: "e2", source: "Accruals!F2", target: "source:JE-900:v1", type: "RECORDED_AS", verified: true, inferred: false },
    { id: "e3", source: "Accruals!F2", target: "blocked:INV-C01-381:v1", type: "EXCLUDED", verified: false, inferred: true },
  ],
};

describe("S2c adapter for main trace responses", () => {
  it("keeps main's status and summary while using graph relationships and source detail", () => {
    const view = toSidebarTrace(trace, graph, request);
    expect(view.status).toBe("review");
    expect(view.headline).toBe(trace.summary);
    expect(view.items.find((item) => item.label === "MSG-0001")).toMatchObject({ relationship: "conflicts", source_id: "MSG-0001:v1", abbr: "EM" });
    expect(view.items.find((item) => item.label === "JE-900")).toMatchObject({ relationship: "context", relationship_text: "Posted entry" });
    expect(view.full_graph_node_id).toBe("artifact:JE-900:v1");
  });

  it("creates formula inputs from main graph edges and the live Excel values", () => {
    const formulaTrace: Trace = {
      ...trace,
      cell: { ...trace.cell, formula: "=E2+E3" },
      lineage: [
        { from: "Accruals!E2", to: "Accruals!F2", kind: "FORMULA_DEPENDS_ON", verified: true },
        { from: "Accruals!E3", to: "Accruals!F2", kind: "FORMULA_DEPENDS_ON", verified: true },
      ],
    };
    const formulaGraph: TraceGraph = {
      ...graph,
      edges: [...graph.edges, ...formulaTrace.lineage.map((item, i) => ({ id: `f${i}`, source: item.from, target: item.to, type: "FORMULA_DEPENDS_ON" as const, verified: true, inferred: false }))],
    };
    const view = toSidebarTrace(formulaTrace, formulaGraph, { ...request, formula: "=E2+E3", precedent_values: { "Accruals!E2": 100, "Accruals!E3": 50 } });
    expect(view.items.filter((item) => item.relationship === "formula").map((item) => [item.abbr, item.amount])).toEqual([["E2", "100"], ["E3", "50"]]);
  });

  it("drops excluded source identity and amount from the sidebar model", () => {
    const view = toSidebarTrace(trace, graph, request);
    const encoded = JSON.stringify(view);
    expect(encoded).not.toContain("INV-C01-381");
    expect(encoded).not.toContain("158.00");
    expect(view.items.find((item) => item.kind === "withheld")).toMatchObject({ label: "Withheld source", amount: null, relationship: "withheld" });
  });

  it("does not create a graph target when the cell has no available recorded-as evidence", () => {
    const withoutPostedEntry = { ...trace, evidence: trace.evidence.filter((item) => item.relationship !== "RECORDED_AS") };
    const view = toSidebarTrace(withoutPostedEntry, graph, request);
    expect(view.full_graph_node_id).toBeNull();
  });

  it("builds the timeline from available dates and the selected snapshot only", () => {
    const view = toSidebarTrace(trace, graph, request);
    expect(view.timeline.map((event) => event.text)).toEqual(["MSG-0001 received", "Posted entry JE-900", "Later document withheld", "You are here"]);
    expect(view.timeline[2]).toMatchObject({ date: null, amount: "", relationship: "later" });
    expect(view.timeline[3].date).toBe(request.as_of);
  });
});
