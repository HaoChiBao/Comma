export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type CaseRow = {
  id: string;
  case_type: string;
  summary: string;
  vendor_name: string;
  decision_at: string;
  recorded_amount: string;
  status: string;
};

export type GraphNode = {
  id: string;
  type: string;
  label: string;
  title: string;
  amount: string | null;
  origin: string;
  version_id: string;
  available_from: string | null;
  document_date: string | null;
  redacted: boolean;
  reason?: string;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  inferred: boolean;
  explanation: string;
  confidence: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok) {
    const code = typeof body?.code === "string" ? body.code : `HTTP_${response.status}`;
    throw new Error(code);
  }
  return body as T;
}
