import type { Actor, InspectRequest, ScanRequest, ScanResult, SourceDetail, Trace, TraceGraph } from "../types";

declare const WORLDSTATE_API_URL: string;

// Empty means same origin: the dev server proxies /v1 and /health to the API, which avoids mixed content.
export const API_URL = typeof WORLDSTATE_API_URL === "string" ? WORLDSTATE_API_URL : "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
  }
}

const MESSAGES: Record<string, string> = {
  BACKEND_UNAVAILABLE: "Comma is not reachable. Your workbook was not changed.",
  FUTURE_INFORMATION: "That source was not available to accounting at this date.",
  UNKNOWN_AVAILABILITY: "When that source became available is unknown, so it is withheld.",
  INSUFFICIENT_PERMISSION: "Your role cannot read that source at this date.",
  OUTSIDE_ORGANIZATION: "That workbook or source belongs to another organization.",
  NOT_IN_SNAPSHOT: "That source is not part of this snapshot.",
  WORKBOOK_NOT_FOUND: "This workbook is not registered with Comma.",
  TRACE_NOT_FOUND: "That trace no longer exists. Inspect the cell again.",
  INDEX_NOT_READY: "Comma is still reading the company documents. Try again in a moment.",
  INVALID_CELL: "Select a single cell.",
  RANGE_TOO_LARGE: "Select at most 200 cells to scan.",
};

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const text = MESSAGES[error.code] ?? error.message;
    return error.requestId ? `${text} (${error.requestId})` : text;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function createClient(baseUrl = API_URL, fetchImpl: typeof fetch = (...args) => fetch(...args)) {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      });
    } catch {
      throw new ApiError(0, "BACKEND_UNAVAILABLE", MESSAGES.BACKEND_UNAVAILABLE);
    }
    if (!response.ok) {
      let body: { code?: string; message?: string; request_id?: string } = {};
      try {
        body = await response.json();
      } catch {
        // Non-JSON error page, e.g. a proxy failure.
      }
      const code = body.code ?? (response.status >= 500 ? "BACKEND_UNAVAILABLE" : `HTTP_${response.status}`);
      throw new ApiError(response.status, code, body.message ?? `Request failed with ${response.status}`, body.request_id);
    }
    return (await response.json()) as T;
  }

  return {
    health: () => request<{ status: string }>("/health"),
    actors: () => request<Actor[]>("/api/v1/actors"),
    inspect: (body: InspectRequest) => request<Trace>("/v1/traces/inspect", { method: "POST", body: JSON.stringify(body) }),
    scan: (body: ScanRequest) => request<ScanResult>("/v1/workbooks/scan", { method: "POST", body: JSON.stringify(body) }),
    graph: (traceId: string) => request<TraceGraph>(`/v1/traces/${encodeURIComponent(traceId)}/graph`),
    source: (sourceId: string, traceId: string) =>
      request<SourceDetail>(`/v1/sources/${encodeURIComponent(sourceId)}?trace_id=${encodeURIComponent(traceId)}`),
  };
}

export type WorldStateClient = ReturnType<typeof createClient>;
