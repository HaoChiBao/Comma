import type { AvailableEvidence, GraphNode, InspectRequest, SidebarTimelineEvent, SidebarTrace, SidebarTraceItem, Trace, TraceGraph } from "./types";
import { formatDate, formatMoney } from "./view";

function addressOf(key: string): string {
  const parts = key.split("!");
  return parts[parts.length - 1] ?? key;
}

function abbreviation(kind: string): string {
  const known: Record<string, string> = {
    purchase_order: "PO",
    email: "EM",
    invoice: "INV",
    journal: "JE",
    contract: "CTR",
    policy: "POL",
  };
  return known[kind] ?? (kind.replace(/[^a-z]/gi, "").slice(0, 3).toUpperCase() || "DOC");
}

function evidenceRelationship(item: AvailableEvidence): SidebarTraceItem["relationship"] {
  switch (item.relationship) {
    case "CONFLICTS_WITH":
      return "conflicts";
    case "SUPPORTED_BY":
      return "supports";
    case "RECORDED_AS":
      return "context";
    default:
      return "shares";
  }
}

function graphRelationship(type: string): SidebarTraceItem["relationship"] | null {
  if (type === "CONFLICTS_WITH") return "conflicts";
  if (type === "SUPPORTED_BY") return "supports";
  if (type === "RECORDED_AS") return "context";
  if (type === "POSSIBLY_RELATED_TO" || type === "MENTIONS" || type === "SOURCE_CANDIDATE" || type === "DERIVED_FROM") return "shares";
  return null;
}

function relationshipText(item: AvailableEvidence): string {
  if (item.comparison === "differs" && item.difference) {
    const amount = formatMoney(item.difference, item.currency);
    return `Conflicts · ${amount} difference`;
  }
  if (item.relationship === "RECORDED_AS") return "Posted entry";
  if (item.link_status === "candidate") return "Possible connection · not confirmed";
  if (item.comparison === "match" || item.comparison === "within_tolerance") return "Agrees with the cell";
  return "Linked evidence";
}

function sourceItem(item: AvailableEvidence): SidebarTraceItem {
  const details: SidebarTraceItem["details"] = [
    { k: "Document", v: item.title },
    { k: "Received", v: formatDate(item.available_at) },
    { k: "Link", v: item.basis },
  ];
  if (item.snippet) details.push({ k: "Says", v: item.snippet });
  if (item.link_status === "candidate") details.push({ k: "Caveat", v: "The link is possible; the API has not verified that the preparer used it." });
  if (item.locator) details.push({ k: "Location", v: item.locator });
  return {
    id: item.source_id,
    kind: item.kind.replace(/_/g, " "),
    label: item.external_id,
    abbr: abbreviation(item.kind),
    amount: item.amount,
    currency: item.currency,
    relationship: evidenceRelationship(item),
    relationship_text: relationshipText(item),
    details,
    source_id: item.source_id,
    available_at: item.available_at,
    cell: item.cell,
  };
}

function inputItem(key: string, value: unknown, index: number, formula: string): SidebarTraceItem {
  const address = addressOf(key);
  const numeric = typeof value === "number";
  return {
    id: `formula:${key}`,
    kind: "cell",
    label: "Formula input",
    abbr: address,
    amount: numeric ? String(value) : null,
    relationship: "formula",
    relationship_text: "Formula input",
    details: [
      { k: "Value", v: numeric ? formatMoney(value) : String(value ?? "—") },
      { k: "Role", v: `Input ${index + 1} of ${formula || "the formula"}` },
    ],
    cell: key,
  };
}

function withheldItems(trace: Trace): SidebarTraceItem[] {
  return trace.evidence.filter((item) => item.status === "excluded").map((item, index) => ({
    // Do not carry the excluded source id, date, amount, or content into the display model.
    id: `withheld:${index}`,
    kind: "withheld",
    label: "Withheld source",
    abbr: "Later",
    amount: null,
    relationship: "withheld",
    relationship_text: item.reason === "FUTURE_INFORMATION" ? "Arrived after this date" : "Not available in this view",
    details: [{ k: "Availability", v: item.reason === "FUTURE_INFORMATION" ? "This document arrived after the selected date." : "This source is withheld for the selected view." }],
    cell: item.cell,
  }));
}

function timelineFor(trace: Trace): SidebarTimelineEvent[] {
  const own = trace.evidence.filter((item): item is AvailableEvidence => item.status === "available" && item.cell === trace.cell.key);
  const events = own
    .filter((item) => item.available_at)
    .map((item): SidebarTimelineEvent => ({
      date: item.available_at,
      text: item.relationship === "RECORDED_AS" ? `Posted entry ${item.external_id}` : `${item.external_id} received`,
      amount: item.amount ? formatMoney(item.amount, item.currency) : "",
      relationship: item.relationship === "RECORDED_AS" ? "close" : evidenceRelationship(item),
    }))
    .sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
  const hasFuture = trace.evidence.some((item) => item.status === "excluded" && item.reason === "FUTURE_INFORMATION");
  if (hasFuture) events.push({ date: null, text: "Later document withheld", amount: "", relationship: "later" });
  events.push({ date: trace.as_of, text: "You are here", amount: "", relationship: "marker" });
  return events;
}

function validSourceNode(node: GraphNode): boolean {
  return node.type !== "SpreadsheetCell" && node.type !== "Blocked";
}

/** Adapts unchanged main API responses to the S2c design; never creates evidence not returned by main. */
export function toSidebarTrace(trace: Trace, graph: TraceGraph, request: InspectRequest): SidebarTrace {
  const graphSources = new Map(graph.nodes.filter(validSourceNode).map((node) => [node.source_id ?? node.id, node]));
  const available = trace.evidence.filter((item): item is AvailableEvidence => item.status === "available");
  const sourceItems = new Map<string, SidebarTraceItem>();
  for (const evidence of available) {
    const item = sourceItem(evidence);
    const node = graphSources.get(evidence.source_id);
    if (node) {
      item.label = node.label || item.label;
      item.abbr = abbreviation(node.type === "Document" ? evidence.kind : node.type.replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase());
    }
    sourceItems.set(evidence.source_id, item);
  }
  const formula = request.formula || "";
  const graphPrecedents = graph.edges.filter((edge) => edge.type === "FORMULA_DEPENDS_ON" && edge.target === trace.cell.key).map((edge) => edge.source);
  const precedentKeys = [...new Set(graphPrecedents.length ? graphPrecedents : trace.lineage.map((edge) => edge.from))];
  const inputs = precedentKeys.map((key, index) => inputItem(key, request.precedent_values[key], index, formula));
  for (const item of sourceItems.values()) {
    const node = graphSources.get(item.source_id ?? "");
    const edge = node && graph.edges.find((candidate) => candidate.target === node.id && candidate.source === trace.cell.key);
    const relationship = edge ? graphRelationship(edge.type) : null;
    if (relationship) item.relationship = relationship;
    if (edge?.inferred && item.relationship === "supports") item.relationship_text = "Possible support · inferred link";
  }
  const items = [...inputs, ...[...sourceItems.values()].sort((a, b) => {
    const rank = (item: SidebarTraceItem) => item.relationship === "conflicts" ? 0 : item.relationship === "supports" ? 1 : 2;
    return rank(a) - rank(b) || a.label.localeCompare(b.label);
  }), ...withheldItems(trace)];
  const formulaDisplay = formula || trace.cell.display_text || (trace.cell.raw_value == null ? "" : String(trace.cell.raw_value));
  const calc = inputs.map((item) => `${item.abbr} ${item.amount == null ? "—" : formatMoney(item.amount)}`).join(" · ");
  return {
    trace_id: trace.trace_id,
    snapshot_id: trace.snapshot_id,
    manifest_hash: trace.manifest_hash,
    as_of: trace.as_of,
    actor_id: trace.actor_id,
    case_id: trace.case_id,
    cell: trace.cell,
    status: trace.status,
    title: trace.title,
    summary: trace.summary,
    difference: trace.difference,
    unknowns: trace.unknowns,
    origin: trace.origin,
    lineage: trace.lineage,
    headline: trace.summary,
    formula_display: formulaDisplay,
    calc,
    items,
    timeline: timelineFor(trace),
  };
}
