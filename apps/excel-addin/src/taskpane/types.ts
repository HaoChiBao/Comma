export type Status = "review" | "unknown" | "supported";

export type Relationship = "SUPPORTED_BY" | "CONFLICTS_WITH" | "POSSIBLY_RELATED_TO" | "RECORDED_AS";

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
  /** The ledger row that records this document's own amount, e.g. `purchase_order_headers.csv:PO-C01-1123`. */
  ledger_locator: string | null;
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

/** The stored claim about where an accrual's amount could come from, kept only when all its evidence is visible. */
export type Origin = {
  claim_type: "derived_from" | "source_candidate" | "unexplained";
  status: "exact" | "ambiguous" | "unexplained";
  formulas: string[];
  explanation: string;
  evidence: string[];
  knowable_from: string;
};

export type Actor = { id: string; name: string; role: string; organization_id: string };

export type Trace = {
  trace_id: string;
  snapshot_id: string;
  manifest_hash: string;
  as_of: string;
  actor_id: string;
  case_id: string | null;
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
  origin: Origin | null;
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
  type: Relationship | "FORMULA_DEPENDS_ON" | "EXCLUDED" | "MENTIONS" | "SOURCE_CANDIDATE" | "DERIVED_FROM";
  verified: boolean;
  inferred: boolean;
  locator?: string;
  comparison?: Comparison;
};

export type TraceGraph = { trace_id: string; as_of: string; nodes: GraphNode[]; edges: GraphEdge[] };

export type ScanFinding = {
  cell: string;
  case_id: string | null;
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
  facts: { fact_id: string; amount: string; currency: string; locator: string; fact_type: string }[];
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
  actor_id: string;
};

export type ScanRequest = {
  workbook_id: string;
  sheet_name: string;
  range_address: string;
  cells: { address: string; raw_value: unknown; formula: string }[];
  as_of: string;
  actor_id: string;
};
