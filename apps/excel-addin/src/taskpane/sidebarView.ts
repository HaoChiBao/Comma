// View model for the sidebar design (S2c: document type inside the ring). Pure functions, no Office.js.

import type { SidebarTimelineEvent as TimelineEvent, SidebarTrace as Trace, SidebarTraceItem as TraceItem, Status } from "./types";

export const INK = "#1F1A17";
export const MUTE = "#6B625C";
export const FAINT = "#B9AFA6";
export const OK = "#2E6B52";
export const REVIEW = "#B5530F";
export const ERROR = "#B42318";
export const UNKNOWN = "#A39A92";
export const ACCENT = "#7A1515";

export const AS_OF_PRESETS = [
  { label: "Sep 30, 2025", short: "Sep 30", note: "Close date", value: "2025-09-30T23:59:00+00:00" },
  { label: "Dec 15, 2025", short: "Dec 15", note: "Later evidence", value: "2025-12-15T12:00:00+00:00" },
];

export const STATUS_VIEW: Record<Status, { label: string; color: string; fill: string; dash: string }> = {
  supported: { label: "Supported", color: OK, fill: "none", dash: "0" },
  review: { label: "Needs review", color: REVIEW, fill: REVIEW, dash: "0" },
  unknown: { label: "No source", color: "#8A817A", fill: "none", dash: "2 2" },
};

const TINT: Record<string, string> = {
  formula: "#EFEAE4",
  supports: "#DCEBE3",
  conflicts: "#F7E3D3",
  shares: "#F7E3D3",
  error: "#F6DAD7",
  withheld: "#EFEAE4",
  context: "#EFEAE4",
};

export const ACTIONS: Record<Status, { label: string; primary: boolean }[]> = {
  supported: [{ label: "Mark reviewed", primary: true }],
  review: [
    { label: "Mark reviewed", primary: true },
    { label: "Ask vendor", primary: false },
  ],
  unknown: [{ label: "Request support", primary: true }],
};

export const LOADING_STEPS = (address: string, asOfShort: string) => [
  `Reading the formula in ${address}`,
  "Searching the ERP, inbox and drive",
  `Rebuilding what was known on ${asOfShort}`,
  "Linking evidence to the number",
];

/** Step timing from the design: one step every 520 ms, done at 2150 ms. */
export const STEP_MS = 520;
export const LOADING_MS = 2150;

/** Which step is active, given time elapsed and how far the real work has got (0 none, 1 Excel read, 2 API answered). */
export function loadingStep(elapsedMs: number, realStep: 0 | 1 | 2): number {
  const timed = Math.min(3, Math.floor(elapsedMs / STEP_MS));
  return Math.min(timed, realStep === 2 ? 3 : realStep);
}

export function loadingDone(elapsedMs: number, realStep: 0 | 1 | 2): boolean {
  return realStep === 2 && elapsedMs >= LOADING_MS;
}

export function relColor(rel: string): string {
  if (rel === "supports") return OK;
  if (rel === "conflicts" || rel === "shares") return REVIEW;
  if (rel === "error") return ERROR;
  if (rel === "formula") return INK;
  return UNKNOWN;
}

export function asOfPreset(value: string) {
  return AS_OF_PRESETS.find((preset) => preset.value === value) ?? AS_OF_PRESETS[0];
}

/** "2025-09-25T17:00:00+00:00" → "Sep 25". Uses the date as written, so an as-of in Pacific time keeps its day. */
export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(match[2]) - 1]} ${Number(match[3])}`;
}

export function money(value: string | number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || value === "") return "";
  const amount = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(amount)) return String(value);
  if (currency !== "USD") return `${currency} ${amount.toFixed(2)}`;
  return `$${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** "$150.00" → "$150", "$160.50" stays. Used inside graph labels. */
export function shortMoney(value: string | number | null): string {
  if (value === null || value === undefined || value === "") return "";
  return money(value).replace(/\.00$/, "");
}

/** Icon for an item in the selected card: fill, stroke and dash, as in the design. */
export function itemIcon(item: TraceItem) {
  const color = relColor(item.relationship);
  const held = item.relationship === "withheld";
  return {
    color,
    fill: item.relationship === "formula" ? "#FFFFFF" : item.relationship === "supports" || held ? "none" : color,
    dash: held ? "1.5 2" : "0",
    textColor: item.relationship === "formula" ? MUTE : color,
  };
}

/** Default selection: the first conflict, else the first document, else nothing. */
export function defaultSelection(trace: Trace): string {
  const items = trace.items;
  const flagged = items.find((item) => item.relationship === "conflicts");
  const document = items.find((item) => item.kind !== "cell");
  return (flagged ?? document)?.id ?? "";
}

export type GraphNodeView = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  ringOpacity: number;
  fill: string;
  tint: string;
  stroke: string;
  strokeWidth: number;
  dash: string;
  opacity: number;
  label: string;
  infoLabel: string | null;
  labelSize: number;
  labelColor: string;
  clickable: boolean;
};

export type GraphEdgeView = { id: string; d: string; stroke: string; width: number; dash: string; opacity: number };

export const GRAPH_W = 328;
export const GRAPH_H = 226;

const f1 = (value: number) => Number(value.toFixed(1));

function edgePath(
  a: { x: number; y: number; width: number },
  b: { x: number; y: number; width: number },
  rel: string,
  id: string,
  width = 2,
): GraphEdgeView {
  const startX = a.x + a.width / 2;
  const endX = b.x - b.width / 2;
  const dx = endX - startX;
  const controlX = dx / 2;
  const d = `M${f1(startX)} ${f1(a.y)}C${f1(startX + controlX)} ${f1(a.y)} ${f1(endX - controlX)} ${f1(b.y)} ${f1(endX)} ${f1(b.y)}`;
  const faint = rel === "withheld" || rel === "context" || rel === "missing";
  return { id, d, stroke: rel === "formula" ? FAINT : relColor(rel), width, dash: faint ? "0.1 8" : "0.1 5", opacity: faint ? 0.8 : 0.95 };
}

function arrivedAfterPeriod(availableAt: string | null | undefined, period: string | null): boolean {
  const match = /^(\d{4})-(\d{2})$/.exec(period ?? "");
  if (!availableAt || !match) return false;
  const monthEnd = new Date(Date.UTC(Number(match[1]), Number(match[2]), 0)).toISOString().slice(0, 10);
  return availableAt.slice(0, 10) > monthEnd;
}

/** The graph keeps formula inputs left, the inspected cell centered, and evidence right in rounded rectangles. */
export function layoutGraph(trace: Trace, selected: string): { nodes: GraphNodeView[]; edges: GraphEdgeView[]; height: number } {
  const X = [40, 124, 262];
  const inputs = trace.items.filter((item) => item.relationship === "formula");
  const sources = trace.items.filter((item) => item.relationship !== "formula");
  const rowHeight = 40;
  const height = Math.max(GRAPH_H, Math.max(sources.length, inputs.length, 1) * rowHeight + 28);
  const C = { x: X[1], y: height / 2, width: 84 };
  const place = (list: TraceItem[], x: number, width: number) => {
    const top = C.y - ((list.length - 1) * rowHeight) / 2;
    return list.map((item, i) => ({ item, x, width, y: list.length === 1 ? C.y : top + i * rowHeight }));
  };
  const left = place(inputs, X[0], 60);
  const right = place(sources, X[2], 104);
  const any = selected !== "";
  const edges: GraphEdgeView[] = [];
  const nodes: GraphNodeView[] = [];

  const itemNode = (p: { item: TraceItem; x: number; y: number }, small: boolean): GraphNodeView => {
    const { item } = p;
    const amount = item.amount ? Number(item.amount) : 0;
    const isSel = selected === item.id;
    const held = item.relationship === "withheld";
    const color = relColor(item.relationship);
    const tint = TINT[item.relationship] ?? "#EFEAE4";
    const opacity = any && !isSel ? 0.6 : 1;
    const label = item.abbr;
    const amountLabel = item.amount ? money(item.amount, item.currency).replace(/\.00$/, "") : null;
    const infoLabel = arrivedAfterPeriod(item.available_at, trace.cell.period)
      ? shortDate(item.available_at ?? null)
      : amountLabel;
    return {
      id: item.id,
      x: p.x,
      y: p.y,
      width: small ? 104 : 60,
      height: small ? 28 : 26,
      ringOpacity: isSel ? 0.5 : 0,
      fill: isSel ? tint : "#FFFFFF",
      tint,
      stroke: color,
      strokeWidth: isSel ? 2.5 : 1.5,
      dash: held ? "2.5 3" : "0",
      opacity,
      label,
      infoLabel,
      labelSize: small ? (label.length > 3 ? 8.5 : 10) : 11.5,
      labelColor: opacity < 1 ? "#8A817A" : held || item.relationship === "context" ? MUTE : INK,
      clickable: true,
    };
  };
  const placeholder = (id: string, label: string, x: number, small: boolean): GraphNodeView => {
    return {
      id,
      x,
      y: C.y,
      width: small ? 104 : 60,
      height: small ? 28 : 26,
      ringOpacity: 0,
      fill: "#FFFFFF",
      tint: "#EFEAE4",
      stroke: UNKNOWN,
      strokeWidth: 1.5,
      dash: "2.5 3",
      opacity: 1,
      label,
      infoLabel: null,
      labelSize: small ? 10 : 11.5,
      labelColor: MUTE,
      clickable: false,
    };
  };

  left.forEach((p) => edges.push(edgePath(p, C, "formula", `in-${p.item.id}`)));
  right.forEach((p) => edges.push(edgePath(C, p, p.item.relationship, `out-${p.item.id}`)));
  if (!right.length) {
    edges.push(edgePath(C, { x: X[2], y: C.y, width: 104 }, "missing", "out-none"));
    nodes.push(placeholder("none", "?", X[2], true));
  }
  if (!left.length) {
    edges.push(edgePath({ x: X[0], y: C.y, width: 60 }, C, "formula", "in-typed"));
    nodes.push(placeholder("typed", "Typed", X[0], false));
  }

  const status = STATUS_VIEW[trace.status];
  const infoLabel = typeof trace.cell.raw_value === "number"
    ? money(trace.cell.raw_value).replace(/\.00$/, "")
    : null;
  const filled = trace.status !== "unknown";
  nodes.push({
    id: trace.cell.key,
    x: C.x,
    y: C.y,
    width: 84,
    height: 32,
    ringOpacity: 0.35,
    fill: filled ? status.color : "#FFFFFF",
    tint: filled ? status.color : "#EFEAE4",
    stroke: status.color,
    strokeWidth: 1.5,
    dash: filled ? "0" : "2.5 3",
    opacity: 1,
    label: trace.cell.address,
    infoLabel,
    labelSize: 11.5,
    labelColor: filled ? "#FFFFFF" : INK,
    clickable: false,
  });
  left.forEach((p) => nodes.push(itemNode(p, false)));
  right.forEach((p) => nodes.push(itemNode(p, true)));
  return { nodes, edges, height };
}

export type TimelineRow = {
  date: string;
  text: string;
  amount: string;
  r: number;
  fill: string;
  stroke: string;
  dash: string;
  weight: number;
  textColor: string;
  opacity: number;
  line: string;
};

export function timelineRows(events: TimelineEvent[]): TimelineRow[] {
  return events.map((event, index) => {
    const line = index === events.length - 1 ? "transparent" : "#CFC6BC";
    const date = shortDate(event.date);
    switch (event.relationship) {
      case "marker":
        return { date, text: event.text, amount: "", r: 5, fill: ACCENT, stroke: ACCENT, dash: "0", weight: 600, textColor: ACCENT, opacity: 1, line };
      case "later":
        return { date, text: event.text, amount: "", r: 4.5, fill: "#FFFFFF", stroke: UNKNOWN, dash: "1.5 2", weight: 400, textColor: INK, opacity: 0.5, line };
      case "close":
        return { date, text: event.text, amount: event.amount, r: 3, fill: INK, stroke: INK, dash: "0", weight: 400, textColor: INK, opacity: 1, line };
      case "missing":
        return { date, text: event.text, amount: "", r: 4.5, fill: "#FFFFFF", stroke: UNKNOWN, dash: "2 2", weight: 400, textColor: INK, opacity: 1, line };
      default:
        return { date, text: event.text, amount: event.amount, r: 4.5, fill: "#FFFFFF", stroke: relColor(event.relationship), dash: "0", weight: 400, textColor: INK, opacity: 1, line };
    }
  });
}

export function countLabel(items: TraceItem[]): string {
  return items.length ? `${items.length} linked` : "none linked";
}

/** True when nothing but formula inputs is linked: the design's "Nothing in the ERP, inbox or drive points to this number." */
export function nothingLinked(items: TraceItem[]): boolean {
  return !items.some((item) => item.relationship !== "formula");
}

export function flaggedRows(findings: { cell: string; address: string; vendor_name?: string; title?: string; status: Status }[]) {
  return findings
    .filter((finding) => finding.status !== "supported")
    .sort((a, b) => a.address.localeCompare(b.address, "en", { numeric: true }))
    .map((finding) => ({ ...finding, vendor_name: finding.vendor_name || finding.title || "", view: STATUS_VIEW[finding.status] }));
}

export const DOT_GRID = (() => {
  const dots: { x: number; y: number; delay: string }[] = [];
  for (let c = 0; c < 27; c++) {
    for (let r = 0; r < 9; r++) dots.push({ x: 8 + c * 12, y: 8 + r * 12, delay: (c * 0.055 + Math.abs(r - 4) * 0.06).toFixed(2) });
  }
  return dots;
})();
