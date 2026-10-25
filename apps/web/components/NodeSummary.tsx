"use client";

import { Fragment, useEffect, useState } from "react";
import { api, type GraphNode } from "@/lib/api";

export type NodeSummaryBody = {
  node_id: string;
  label: string;
  what: string;
  relationships: string;
  connected_to: string;
  risks: string;
  summary: string;
};

export function useNodeSummary(nodeId: string | null, actorId: string, asOf: string, mode: string, organizationId: string) {
  const [summary, setSummary] = useState<NodeSummaryBody | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!nodeId || !actorId) {
      setSummary(null);
      setError("");
      return;
    }
    let cancel = false;
    setSummary(null);
    setError("");
    const params = new URLSearchParams({
      node_id: nodeId,
      actor_id: actorId,
      as_of: asOf,
      mode,
      organization_id: organizationId,
    });
    const started = Date.now();
    api<NodeSummaryBody>(`/api/v1/company-graph/summary?${params}`)
      .then(async (body) => {
        const wait = 900 - (Date.now() - started);
        if (wait > 0) await new Promise((resolve) => window.setTimeout(resolve, wait));
        if (!cancel) setSummary(body);
      })
      .catch(() => {
        if (!cancel) setError("This node is not in the snapshot.");
      });
    return () => {
      cancel = true;
    };
  }, [nodeId, actorId, asOf, mode, organizationId]);

  return { summary, error };
}

function SummaryPlaceholder() {
  return (
    <div className="summary-skeleton" aria-busy="true" aria-label="Reading this node">
      {["What it is", "Relationships", "Connected to", "Risks"].map((label) => (
        <div key={label}>
          <span className="summary-skeleton-label">{label}</span>
          <span className="summary-skeleton-line" />
          <span className="summary-skeleton-line short" />
        </div>
      ))}
    </div>
  );
}

export function NodeSummary({
  summary,
  error,
  nodes,
  onOpen,
}: {
  summary: NodeSummaryBody | null;
  error: string;
  nodes: GraphNode[];
  onOpen: (id: string) => void;
}) {
  if (error) return <p className="bad">{error}</p>;
  if (!summary) return <SummaryPlaceholder />;
  const sections = [
    ["What it is", summary.what],
    ["Relationships", summary.relationships],
    ["Connected to", summary.connected_to],
    ["Risks", summary.risks],
  ] as const;
  return (
    <div className="node-summary">
      <dl>
        {sections.map(([label, text]) =>
          text ? (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{linkRecords(text, nodes, summary.node_id, onOpen)}</dd>
            </div>
          ) : null,
        )}
      </dl>
    </div>
  );
}

function linkRecords(text: string, nodes: GraphNode[], currentId: string, onOpen: (id: string) => void) {
  const byLabel = new Map<string, string>();
  for (const node of nodes) {
    const label = node.label?.trim();
    if (!label || label.length < 3 || node.id === currentId || byLabel.has(label)) continue;
    byLabel.set(label, node.id);
  }
  const labels = [...byLabel.keys()].filter((label) => text.includes(label)).sort((a, b) => b.length - a.length);
  if (labels.length === 0) return text;
  const pattern = new RegExp(labels.map((label) => escapeRegExp(label)).join("|"), "g");
  const parts: Array<string | { label: string; id: string; at: number }> = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    const label = match[0];
    const id = byLabel.get(label);
    if (!id || !bounded(text, at, label.length)) continue;
    if (at > cursor) parts.push(text.slice(cursor, at));
    parts.push({ label, id, at });
    cursor = at + label.length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts.map((part, index) =>
    typeof part === "string" ? (
      <Fragment key={`t-${index}`}>{part}</Fragment>
    ) : (
      <button key={`n-${part.at}`} type="button" className="summary-ref" onClick={() => onOpen(part.id)}>
        {part.label}
      </button>
    )
  );
}

function bounded(text: string, at: number, length: number) {
  const before = at === 0 ? "" : text[at - 1];
  const after = text[at + length] ?? "";
  return !isToken(before) && !isToken(after);
}

function isToken(char: string) {
  return /[A-Za-z0-9]/.test(char);
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
