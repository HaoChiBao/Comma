export type Status = "review" | "unknown" | "supported";

export type Relationship = "SUPPORTED_BY" | "CONFLICTS_WITH" | "POSSIBLY_RELATED_TO";

export type Comparison =
  | "match"
  | "within_tolerance"
  | "differs"
  | "vendor_mismatch"
  | "not_comparable_currency"
  | "not_comparable_period"
  | "no_amount";

export type AvailableEvidence = {
  cell: string;
  source_id: string;
  status: "available";
  external_id: string;
  kind: string;
  title: string;
  amount: string | null;
  currency: string;
  period: string | null;
  document_date: string;
  available_at: string | null;
  locator: string;
  snippet: string;
  relationship: Relationship;
  link_status: "verified" | "candidate";
  basis: string;
  comparison: Comparison;
  difference: string | null;
};

export type ExcludedEvidence = {
  cell: string;
  source_id: string;
  status: "excluded";
  reason: string;
  relationship: Relationship;
  link_status: "verified" | "candidate";
};

export type Evidence = AvailableEvidence | ExcludedEvidence;

export type Trace = {
  trace_id: string;
  snapshot_id: string;
  manifest_hash: string;
  as_of: string;
  actor_id: string;
  cell: {
    key: string;
    sheet_name: string;
    address: string;
    raw_value: unknown;
    display_text: string | null;
    formula: string;
    registered: boolean;
    vendor_name: string | null;
    period: string | null;
    reference: string | null;
  };
  status: Status;
  title: string;
  summary: string;
  difference: { amount: string; currency: string } | null;
  unknowns: string[];
  lineage: { from: string; to: string; kind: "FORMULA_DEPENDS_ON"; verified: boolean }[];
  evidence: Evidence[];
};

export type GraphNode = {
  id: string;
  type: string;
  label: string;
  title: string;
  amount: string | null;
  status?: Status;
  selected?: boolean;
  reason?: string;
  source_id?: string | null;
  available_at?: string | null;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: Relationship | "FORMULA_DEPENDS_ON" | "EXCLUDED";
  verified: boolean;
  inferred: boolean;
  locator?: string;
  comparison?: Comparison;
};

export type TraceGraph = { trace_id: string; as_of: string; nodes: GraphNode[]; edges: GraphEdge[] };

export type ScanFinding = {
  cell: string;
  sheet_name: string;
  address: string;
  status: Status;
  title: string;
  summary: string;
  difference: { amount: string; currency: string } | null;
  unknowns: string[];
};

export type ScanResult = {
  snapshot_id: string;
  as_of: string;
  scanned: number;
  counts: Partial<Record<Status, number>>;
  findings: ScanFinding[];
};

export type SourceDetail = {
  source_id: string;
  external_id: string;
  kind: string;
  title: string;
  content_text: string;
  content_hash: string;
  document_date: string;
  available_at: string | null;
  availability_evidence: string;
  facts: { fact_id: string; amount: string; currency: string; locator: string }[];
  trace_id: string;
  as_of: string;
};

export type InspectRequest = {
  workbook_id: string;
  sheet_name: string;
  cell_address: string;
  raw_value: unknown;
  display_text: string;
  formula: string;
  direct_precedents: string[];
  precedent_values: Record<string, unknown>;
  as_of: string;
};

export type ScanRequest = {
  workbook_id: string;
  sheet_name: string;
  range_address: string;
  cells: { address: string; raw_value: unknown; formula: string }[];
  as_of: string;
};
