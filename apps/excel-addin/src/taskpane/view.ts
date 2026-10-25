import type { AvailableEvidence, ExcludedEvidence, GraphEdge, GraphNode, Status, Trace, TraceGraph } from "./types";

export const AS_OF_PRESETS = [
  { label: "Sep 30, 2025", value: "2025-09-30T23:59:59-07:00" },
  { label: "Dec 15, 2025", value: "2025-12-15T12:00:00-08:00" },
];

export const STATUS_VIEW: Record<Status, { label: string; tone: string }> = {
  review: { label: "Review", tone: "review" },
  unknown: { label: "Unknown", tone: "unknown" },
  supported: { label: "Supported", tone: "supported" },
};

export const RELATIONSHIP_LABEL: Record<string, string> = {
  FORMULA_DEPENDS_ON: "formula",
  SUPPORTED_BY: "supported by",
  CONFLICTS_WITH: "conflicts with",
  POSSIBLY_RELATED_TO: "possibly related",
  EXCLUDED: "withheld",
};

export const REASON_LABEL: Record<string, string> = {
  FUTURE_INFORMATION: "Arrived after this date",
  UNKNOWN_AVAILABILITY: "Availability date unknown",
  INSUFFICIENT_PERMISSION: "Not readable by this role at this date",
  OUTSIDE_ORGANIZATION: "Another organization",
  NOT_IN_SNAPSHOT: "Not in this snapshot",
};

export type EvidenceView = {
  available: AvailableEvidence[];
  excluded: ExcludedEvidence[];
};

/** Cell's own evidence first, then evidence reached through formula precedents. */
export function evidenceView(trace: Trace): EvidenceView {
  const own = (item: { cell: string }) => (item.cell === trace.cell.key ? 0 : 1);
  const available = trace.evidence.filter((item): item is AvailableEvidence => item.status === "available");
  const excluded = trace.evidence.filter((item): item is ExcludedEvidence => item.status === "excluded");
  available.sort((a, b) => own(a) - own(b) || conflictRank(a) - conflictRank(b) || a.cell.localeCompare(b.cell));
  return { available, excluded };
}

function conflictRank(item: AvailableEvidence): number {
  return item.relationship === "CONFLICTS_WITH" ? 0 : item.link_status === "verified" ? 1 : 2;
}

export function comparisonLabel(item: AvailableEvidence): string {
  switch (item.comparison) {
    case "match":
      return "matches";
    case "within_tolerance":
      return `within tolerance (${formatMoney(item.difference)})`;
    case "differs":
      return `differs by ${formatMoney(item.difference)}`;
    case "vendor_mismatch":
      return "different vendor";
    case "not_comparable_currency":
      return `not compared (${item.currency})`;
    case "not_comparable_period":
      return `not compared (${item.period})`;
    default:
      return "no amount";
  }
}

export function formatMoney(value: string | number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(amount)) return String(value);
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export type Placed = GraphNode & { x: number; y: number };

export const NODE_W = 96;
export const NODE_H = 40;

/**
 * Three fixed columns: formula precedents, the selected cell, then sources.
 * Small enough for a 320px task pane without a physics layout.
 */
export function layoutGraph(graph: TraceGraph, width = 320): { nodes: Placed[]; edges: GraphEdge[]; height: number } {
  const selected = graph.nodes.find((node) => node.selected);
  const precedentIds = new Set(graph.edges.filter((edge) => edge.type === "FORMULA_DEPENDS_ON").map((edge) => edge.source));
  const columns: GraphNode[][] = [[], [], []];
  for (const node of graph.nodes) {
    if (node === selected) columns[1].push(node);
    else if (precedentIds.has(node.id)) columns[0].push(node);
    else columns[2].push(node);
  }
  columns[2].sort((a, b) => Number(a.type === "Blocked") - Number(b.type === "Blocked") || a.label.localeCompare(b.label));
  const gap = 16;
  const tallest = Math.max(...columns.map((column) => column.length), 1);
  const height = tallest * (NODE_H + gap) + gap;
  const xs = [4, (width - NODE_W) / 2, width - NODE_W - 4];
  const nodes: Placed[] = [];
  columns.forEach((column, index) => {
    const offset = (height - column.length * (NODE_H + gap) + gap) / 2;
    column.forEach((node, row) => nodes.push({ ...node, x: xs[index], y: offset + row * (NODE_H + gap) }));
  });
  return { nodes, edges: graph.edges, height };
}

export const EDGE_STYLE: Record<string, { stroke: string; dash?: string }> = {
  FORMULA_DEPENDS_ON: { stroke: "#2563eb" },
  SUPPORTED_BY: { stroke: "#15803d" },
  CONFLICTS_WITH: { stroke: "#b45309" },
  POSSIBLY_RELATED_TO: { stroke: "#6b7280", dash: "4 3" },
  EXCLUDED: { stroke: "#9ca3af", dash: "1 3" },
};

export function edgeStyle(edge: GraphEdge): { stroke: string; dash?: string } {
  const base = EDGE_STYLE[edge.type] ?? EDGE_STYLE.POSSIBLY_RELATED_TO;
  return edge.inferred && !base.dash ? { ...base, dash: "4 3" } : base;
}
