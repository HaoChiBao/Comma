import { describe, expect, it } from "vitest";
import { ApiError, createClient, describeError } from "../src/taskpane/services/worldstateApi";
import type { Trace, TraceGraph } from "../src/taskpane/types";
import { comparisonLabel, evidenceView, formatMoney, layoutGraph, STATUS_VIEW } from "../src/taskpane/view";

function respond(status: number, body: unknown): typeof fetch {
  return async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const trace: Trace = {
  trace_id: "trc_1",
  snapshot_id: "snap_1",
  manifest_hash: "abc123abc123abc123",
  as_of: "2025-09-30T23:59:59-07:00",
  actor_id: "actor-maya",
  cell: {
    key: "Accruals!D3",
    sheet_name: "Accruals",
    address: "D3",
    raw_value: 150,
    display_text: "$150.00",
    formula: "=B3+C3",
    registered: true,
    vendor_name: "Cloud Services Inc.",
    period: "2025-09",
    reference: "PO-104",
  },
  status: "review",
  title: "Potential estimate discrepancy",
  summary: "EM-208 shows $160.00; this cell is $150.00.",
  difference: { amount: "10.00", currency: "USD" },
  unknowns: [],
  lineage: [
    { from: "Accruals!B3", to: "Accruals!D3", kind: "FORMULA_DEPENDS_ON", verified: true },
    { from: "Accruals!C3", to: "Accruals!D3", kind: "FORMULA_DEPENDS_ON", verified: true },
  ],
  evidence: [
    available("Accruals!B3", "PO-104:v1", "100.00", "SUPPORTED_BY", "verified", "match"),
    available("Accruals!D3", "PO-104:v1", "150.00", "SUPPORTED_BY", "verified", "match"),
    available("Accruals!D3", "EM-208:v1", "160.00", "CONFLICTS_WITH", "candidate", "differs", "10.00"),
    { cell: "Accruals!D3", source_id: "INV-104:v1", status: "excluded", reason: "FUTURE_INFORMATION", relationship: "POSSIBLY_RELATED_TO", link_status: "candidate" },
  ],
};

function available(cell: string, source_id: string, amount: string, relationship: any, link_status: any, comparison: any, difference: string | null = null) {
  return {
    cell,
    source_id,
    status: "available" as const,
    external_id: source_id.split(":")[0],
    kind: source_id.startsWith("EM") ? "email" : "purchase_order",
    title: source_id,
    amount,
    currency: "USD",
    period: "2025-09",
    document_date: "2025-09-12T16:00:00+00:00",
    available_at: "2025-09-12T16:00:00+00:00",
    locator: "total",
    snippet: "",
    relationship,
    link_status,
    basis: "",
    comparison,
    difference,
  };
}

describe("API client", () => {
  it("returns parsed JSON on success", async () => {
    const client = createClient("", respond(200, trace));
    expect((await client.inspect({} as any)).status).toBe("review");
  });

  it("maps typed errors to user messages with the request id", async () => {
    const client = createClient("", respond(403, { code: "FUTURE_INFORMATION", message: "denied", request_id: "req_1" }));
    const error = await client.source("INV-104:v1", "trc_1").catch((err) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("FUTURE_INFORMATION");
    expect(describeError(error)).toBe("That source was not available to accounting at this date. (req_1)");
  });

  it("reports an unreachable backend without throwing a raw TypeError", async () => {
    const client = createClient("", async () => {
      throw new TypeError("Failed to fetch");
    });
    const error = await client.health().catch((err) => err);
    expect(error.code).toBe("BACKEND_UNAVAILABLE");
    expect(describeError(error)).toMatch(/not reachable.*not changed/);
  });

  it("treats a non-JSON 502 from the proxy as backend unavailable", async () => {
    const client = createClient("", async () => new Response("Bad gateway", { status: 502 }));
    const error = await client.health().catch((err) => err);
    expect(error.code).toBe("BACKEND_UNAVAILABLE");
  });
});

describe("view model", () => {
  it("labels statuses without calling anything incorrect", () => {
    expect(Object.values(STATUS_VIEW).map((view) => view.label)).toEqual(["Review", "Unknown", "Supported"]);
  });

  it("puts the cell's own conflicts first and keeps withheld sources separate", () => {
    const view = evidenceView(trace);
    expect(view.available.map((item) => `${item.cell}:${item.source_id}`)).toEqual([
      "Accruals!D3:EM-208:v1",
      "Accruals!D3:PO-104:v1",
      "Accruals!B3:PO-104:v1",
    ]);
    expect(view.excluded).toHaveLength(1);
    expect(JSON.stringify(view.excluded)).not.toMatch(/158/);
  });

  it("describes comparisons", () => {
    const [conflict] = evidenceView(trace).available;
    expect(comparisonLabel(conflict)).toBe("differs by $10.00");
    expect(comparisonLabel({ ...conflict, comparison: "not_comparable_currency", currency: "EUR" })).toBe("not compared (EUR)");
    expect(formatMoney("150")).toBe("$150.00");
    expect(formatMoney(null)).toBe("—");
  });

  it("lays out precedents, the cell, and sources in three columns", () => {
    const graph: TraceGraph = {
      trace_id: "trc_1",
      as_of: trace.as_of,
      nodes: [
        { id: "Accruals!D3", type: "SpreadsheetCell", label: "D3", title: "", amount: "150.00", selected: true },
        { id: "Accruals!B3", type: "SpreadsheetCell", label: "B3", title: "", amount: "100.00" },
        { id: "blocked:INV-104:v1", type: "Blocked", label: "Not yet available", title: "", amount: null },
        { id: "source:EM-208:v1", type: "Email", label: "EM-208", title: "", amount: "160.00" },
      ],
      edges: [
        { id: "e1", source: "Accruals!B3", target: "Accruals!D3", type: "FORMULA_DEPENDS_ON", verified: true, inferred: false },
        { id: "e2", source: "Accruals!D3", target: "source:EM-208:v1", type: "CONFLICTS_WITH", verified: false, inferred: true },
      ],
    };
    const { nodes } = layoutGraph(graph, 320);
    const x = Object.fromEntries(nodes.map((node) => [node.id, node.x]));
    expect(x["Accruals!B3"]).toBeLessThan(x["Accruals!D3"]);
    expect(x["Accruals!D3"]).toBeLessThan(x["source:EM-208:v1"]);
    const sources = nodes.filter((node) => node.x === x["source:EM-208:v1"]).map((node) => node.id);
    expect(sources).toEqual(["source:EM-208:v1", "blocked:INV-104:v1"]);
  });
});
