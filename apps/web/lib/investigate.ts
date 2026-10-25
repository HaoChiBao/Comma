import type { GraphNode } from "@/lib/api";

export type InvestigationStep = {
  kind: "Thinking" | "Action" | "Verdict";
  text: string;
  lines?: string[];
};

export type AgentSuggestion = {
  id: string;
  anchorId: string;
  label: string;
  amount: string | null;
  type: string;
  edgeType: string;
  reason: string;
  ruleId: string;
};

export type InferredLink = {
  id: string;
  source: string;
  target: string;
};

export type Precedent = {
  id: string;
  kind: "visibility" | "learned";
  rule: string;
  recordIds: string[];
  kept: boolean;
};

export type CaseBrief = { id: string; vendor: string };

const STORAGE_KEY = "comma.learned-precedents";

const CLAIM_TYPES = new Set(["CONTRADICTS", "SOURCE_CANDIDATE", "DERIVED_FROM", "AMBIGUOUS"]);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function visibilityPrecedents(): Precedent[] {
  return [
    {
      id: "visibility:snapshot",
      kind: "visibility",
      rule: "Only suggest records the current snapshot already returned for this actor and as-of. Deprecating this stops suggestions. It does not load withheld records.",
      recordIds: [],
      kept: true,
    },
    {
      id: "visibility:undrawn-neighbor",
      kind: "visibility",
      rule: "Suggest a visible neighbor that is not already drawn on the graph.",
      recordIds: [],
      kept: true,
    },
    {
      id: "visibility:earlier-amount",
      kind: "visibility",
      rule: "Suggest an earlier visible amount in the same case when that record is not already drawn.",
      recordIds: [],
      kept: true,
    },
    {
      id: "visibility:claim-link",
      kind: "visibility",
      rule: "Suggest an undrawn record linked by CONTRADICTS, source candidate, derived, or ambiguous.",
      recordIds: [],
      kept: true,
    },
    {
      id: "visibility:withheld",
      kind: "visibility",
      rule: "Do not suggest a record the snapshot marks withheld. Deprecating this does not reveal it.",
      recordIds: [],
      kept: true,
    },
  ];
}

export function loadPrecedents(): Precedent[] {
  const seeded = visibilityPrecedents();
  if (typeof window === "undefined") return seeded;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return seeded;
    const saved = JSON.parse(raw) as Precedent[];
    if (!Array.isArray(saved)) return seeded;
    const keptById = new Map(saved.filter((item) => item && item.id).map((item) => [item.id, item.kept !== false]));
    const visibility = seeded.map((item) => ({ ...item, kept: keptById.has(item.id) ? Boolean(keptById.get(item.id)) : item.kept }));
    const learned = saved.filter((item) => item && item.kind === "learned" && item.id && item.rule);
    return [...learned, ...visibility];
  } catch {
    return seeded;
  }
}

export function savePrecedents(precedents: Precedent[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(precedents));
}

export function learnPrecedent(suggestion: AgentSuggestion, anchorLabel: string): Precedent {
  return {
    id: `learned:${suggestion.anchorId}:${suggestion.id}`,
    kind: "learned",
    rule: `When investigating ${anchorLabel}, suggest ${suggestion.label} because ${suggestion.reason}.`,
    recordIds: [suggestion.anchorId, suggestion.id],
    kept: true,
  };
}

type Linked = { node: GraphNode; types: string[] };

export function buildInvestigation(input: {
  node: GraphNode;
  nodes: GraphNode[];
  edges: { source: string; target: string; type: string }[];
  cases: CaseBrief[];
  drawnIds: string[];
  precedents: Precedent[];
  blockedIds: string[];
}): { steps: InvestigationStep[]; suggestions: AgentSuggestion[] } {
  const { node } = input;
  const drawn = new Set(input.drawnIds);
  const blocked = new Set(input.blockedIds);
  const kept = new Map(input.precedents.map((item) => [item.id, item.kept]));
  const ruleOn = (id: string) => kept.get(id) !== false;
  const linked = canonicalLinks(node, input.nodes, input.edges);
  const same = linked.filter((item) => sameCase(node, item.node));
  const other = linked.filter((item) => !sameCase(node, item.node));
  const earlierSame = same.filter((item) => item.node.amount && isEarlier(item.node, node));
  const earlierOther = other.filter((item) => item.node.amount && isEarlier(item.node, node));
  const exactEarlier = [...earlierSame, ...earlierOther].filter((item) => moneyEqual(item.node.amount, node.amount));
  const laterSame = same.filter((item) => item.node.amount && isLater(item.node, node));
  const vendor = input.cases.find((item) => item.id && item.id === node.case_id)?.vendor || "";

  const compared = dedupeLinked(earlierSame.length ? earlierSame : same.length ? same : exactEarlier);
  const steps: InvestigationStep[] = [
    { kind: "Thinking", text: thinkingReview(node, vendor, earlierSame) },
    { kind: "Action", text: actionRetrieved(node, compared), lines: actionLines(compared, node) },
    { kind: "Verdict", text: verdict(node, earlierSame, laterSame, exactEarlier) },
  ];

  const suggestions = ruleOn("visibility:snapshot")
    ? pickSuggestions(node, linked, drawn, blocked, input.precedents, ruleOn)
    : [];

  return { steps, suggestions };
}

function pickSuggestions(
  node: GraphNode,
  linked: Linked[],
  drawn: Set<string>,
  blocked: Set<string>,
  precedents: Precedent[],
  ruleOn: (id: string) => boolean
): AgentSuggestion[] {
  const chosen: AgentSuggestion[] = [];
  const seen = new Set<string>();

  const consider = (item: Linked, ruleId: string, reason: string) => {
    if (chosen.length >= 3) return;
    const other = item.node;
    if (!other?.id || seen.has(other.id) || other.id === node.id) return;
    if (drawn.has(other.id) || blocked.has(other.id)) return;
    if (!isDocument(other) || other.label === node.label) return;
    if (pairDeprecated(precedents, node.id, other.id)) return;
    seen.add(other.id);
    chosen.push({
      id: other.id,
      anchorId: node.id,
      label: other.label,
      amount: other.amount,
      type: other.type,
      edgeType: item.types[0] || "LINK",
      reason,
      ruleId,
    });
  };

  if (ruleOn("visibility:claim-link")) {
    for (const item of linked) {
      if (!sameCase(node, item.node)) continue;
      const claim = item.types.find((type) => CLAIM_TYPES.has(type));
      if (!claim) continue;
      consider(item, "visibility:claim-link", `it is a ${claim} link in ${node.case_id || "this snapshot"} and is not already drawn`);
    }
  }
  if (ruleOn("visibility:earlier-amount")) {
    for (const item of linked) {
      if (!sameCase(node, item.node) || !item.node.amount || !isEarlier(item.node, node)) continue;
      consider(item, "visibility:earlier-amount", "it is an earlier visible amount in the same case and is not already drawn");
    }
  }
  if (ruleOn("visibility:undrawn-neighbor")) {
    for (const item of linked) {
      if (!sameCase(node, item.node)) continue;
      consider(item, "visibility:undrawn-neighbor", "it is a visible neighbor and is not already drawn");
    }
  }
  if (ruleOn("visibility:claim-link")) {
    for (const item of linked) {
      const claim = item.types.find((type) => CLAIM_TYPES.has(type));
      if (!claim) continue;
      consider(item, "visibility:claim-link", `it is a ${claim} link and is not already drawn`);
    }
  }
  return chosen;
}

function pairDeprecated(precedents: Precedent[], anchorId: string, nodeId: string) {
  return precedents.some(
    (item) =>
      item.kind === "learned" &&
      item.kept === false &&
      item.recordIds.includes(anchorId) &&
      item.recordIds.includes(nodeId)
  );
}

function thinkingReview(node: GraphNode, vendor: string, earlierSame: Linked[]) {
  const who = vendor && node.case_id ? `${node.case_id} for ${vendor}` : node.case_id || vendor || "this case";
  if (!node.amount) {
    return `${node.label} has no amount. The review is limited to the other records already visible for ${who}.`;
  }
  const mismatched = earlierSame.filter((item) => item.node.amount && !moneyEqual(item.node.amount, node.amount));
  if (earlierSame.length === 0) {
    return `${node.label} records ${dollars(node.amount)}. Nothing earlier in ${who} is available to compare.`;
  }
  if (mismatched.length === 0) {
    return `${node.label} records ${dollars(node.amount)}. The earlier amounts in ${who} match it.`;
  }
  const spread = amountSpread([node, ...earlierSame.map((item) => item.node)]);
  const range = spread ? ` They run from ${spread.low} to ${spread.high}.` : "";
  const count = mismatched.length === 1 ? "One earlier amount" : `${countWord(mismatched.length)} earlier amounts`;
  const verb = mismatched.length === 1 ? "does" : "do";
  return `${node.label} records ${dollars(node.amount)}. ${count} in ${who} ${verb} not match it.${range}`;
}

function actionRetrieved(node: GraphNode, compared: Linked[]) {
  if (compared.length === 0) return `No other record in this snapshot is linked to ${node.label}.`;
  const word = compared.length === 1 ? "record" : "records";
  return `Compared ${compared.length} ${word} already visible with ${node.label}.`;
}

function verdict(node: GraphNode, earlierSame: Linked[], laterSame: Linked[], exactEarlier: Linked[]) {
  if (!node.amount) {
    return `There is no amount on ${node.label} to reconcile.`;
  }
  const sentences: string[] = [];
  if (exactEarlier.length === 0) {
    sentences.push(`No earlier visible amount equals ${dollars(node.amount)}.`);
  } else if (exactEarlier.length === 1) {
    const hit = exactEarlier[0];
    const where = hit.node.case_id && hit.node.case_id !== node.case_id ? ` in ${hit.node.case_id}` : "";
    sentences.push(`${describe(hit)}${where} equals ${dollars(node.amount)}. One match is a candidate, not proof it was the source.`);
  } else {
    sentences.push(`${exactEarlier.map((item) => item.node.label).join(", ")} all equal ${dollars(node.amount)}. More than one match leaves the source ambiguous.`);
  }
  const mismatches = earlierSame.filter((item) => item.node.amount && !moneyEqual(item.node.amount, node.amount));
  if (mismatches.length > 0) {
    sentences.push(`${mismatches.map((item) => `${item.node.label} is ${deltaPhrase(item.node.amount, node.amount)}`).join(". ")}.`);
  }
  if (laterSame.length > 0) {
    const verb = laterSame.length === 1 ? "is" : "are";
    sentences.push(`${laterSame.map(describe).join(", ")} ${verb} knowable after ${prettyDate(stamp(node) || "")}.`);
  }
  return sentences.join(" ");
}

function canonicalLinks(
  node: GraphNode,
  nodes: GraphNode[],
  edges: { source: string; target: string; type: string }[]
): Linked[] {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const byNode = new Map<string, Linked>();
  for (const edge of edges) {
    if (edge.source !== node.id && edge.target !== node.id) continue;
    const otherId = edge.source === node.id ? edge.target : edge.source;
    const other = byId.get(otherId);
    if (!other || other.id === node.id || !isDocument(other) || other.label === node.label) continue;
    const existing = byNode.get(other.id);
    if (existing) {
      if (!existing.types.includes(edge.type)) existing.types.push(edge.type);
      continue;
    }
    byNode.set(other.id, { node: other, types: [edge.type || "LINK"] });
  }
  const byLabel = new Map<string, Linked>();
  for (const item of byNode.values()) {
    const key = item.node.label || item.node.id;
    const existing = byLabel.get(key);
    if (!existing || rank(item.node, node) > rank(existing.node, node)) {
      const types = [...new Set([...(existing?.types || []), ...item.types])];
      byLabel.set(key, { node: item.node, types });
    } else if (existing) {
      for (const type of item.types) if (!existing.types.includes(type)) existing.types.push(type);
    }
  }
  return [...byLabel.values()];
}

function dedupeLinked(items: Linked[]) {
  const seen = new Set<string>();
  const result: Linked[] = [];
  for (const item of items) {
    if (seen.has(item.node.id)) continue;
    seen.add(item.node.id);
    result.push(item);
  }
  return result;
}

function rank(item: GraphNode, focus: GraphNode) {
  let score = 0;
  if (item.case_id && item.case_id === focus.case_id) score += 4;
  if (item.id.startsWith("artifact:")) score += 2;
  if (item.amount) score += 1;
  return score;
}

function isDocument(node: GraphNode) {
  if (node.redacted) return false;
  if (node.type === "TRANSACTION" || node.type === "VENDOR" || node.type === "ENTITY") return false;
  if (node.origin === "entity") return false;
  return true;
}

function sameCase(left: GraphNode, right: GraphNode) {
  return Boolean(left.case_id) && left.case_id === right.case_id;
}

function stamp(node: GraphNode) {
  return node.available_from || node.document_date || "";
}

function isEarlier(other: GraphNode, node: GraphNode) {
  const left = stamp(other);
  const right = stamp(node);
  return Boolean(left && right && left < right);
}

function isLater(other: GraphNode, node: GraphNode) {
  const left = stamp(other);
  const right = stamp(node);
  return Boolean(left && right && left > right);
}

function moneyEqual(left: string | null, right: string | null) {
  if (!left || !right) return false;
  const a = Number(left);
  const b = Number(right);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return left === right;
  return Math.abs(a - b) < 0.001;
}

function dollars(amount: string) {
  return `$${amount}`;
}

function describe(item: Linked) {
  return item.node.amount ? `${item.node.label} ${dollars(item.node.amount)}` : item.node.label;
}

function actionLines(compared: Linked[], node: GraphNode) {
  const shown = compared.slice(0, 6);
  const extra = compared.length - shown.length;
  const lines = shown.map(recordLine);
  if (extra > 0) lines.push(`${extra} more in ${node.case_id || "this case"}`);
  return lines;
}

function recordLine(item: Linked) {
  const amount = item.node.amount ? dollars(item.node.amount) : "No amount";
  const date = stamp(item.node) ? prettyDate(stamp(item.node)) : "Date unknown";
  return `${item.node.label} · ${amount} · ${date}`;
}

function amountSpread(nodes: GraphNode[]) {
  const values = nodes
    .map((item) => (item.amount ? Number(item.amount) : NaN))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2) return null;
  const low = Math.min(...values);
  const high = Math.max(...values);
  if (Math.abs(high - low) < 0.001) return null;
  return { low: money(low), high: money(high) };
}

function deltaPhrase(other: string | null, focus: string | null) {
  const left = Number(other);
  const right = Number(focus);
  if (!Number.isFinite(left) || !Number.isFinite(right)) return "a different amount";
  const diff = Math.abs(left - right);
  const direction = left > right ? "higher" : "lower";
  return `${money(diff)} ${direction}`;
}

function money(value: number) {
  return `$${value.toFixed(2)}`;
}

function countWord(count: number) {
  return ["One", "Two", "Three", "Four", "Five", "Six"][count - 1] || String(count);
}

function prettyDate(iso: string) {
  if (!iso) return "an unknown date";
  const month = Number(iso.slice(5, 7));
  const day = Number(iso.slice(8, 10));
  const year = iso.slice(0, 4);
  if (!day || month < 1 || month > 12) return iso.slice(0, 10);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}
