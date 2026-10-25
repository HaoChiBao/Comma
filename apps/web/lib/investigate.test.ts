import assert from "node:assert/strict";
import test from "node:test";
import { buildInvestigation, visibilityPrecedents, type Precedent } from "./investigate.ts";
import type { GraphNode } from "./api.ts";

function record(partial: Partial<GraphNode> & { id: string }): GraphNode {
  return {
    type: "DOCUMENT",
    label: partial.label || partial.id,
    title: partial.label || partial.id,
    amount: partial.amount ?? "100.00",
    origin: partial.origin || "sourced",
    version_id: partial.id,
    available_from: partial.available_from ?? "2025-09-01T00:00:00",
    document_date: partial.document_date ?? "2025-09-01T00:00:00",
    redacted: false,
    case_id: partial.case_id ?? "C03",
    document_id: partial.document_id,
    ...partial,
  };
}

const anchor = record({
  id: "artifact:amd",
  label: "Contract Amendment - Revised contract pricing - 2025-09-20 - $4,600.00",
  document_id: "AMD-001",
  type: "CONTRACT",
  amount: "4600.00",
  available_from: "2025-10-02T14:00:00",
  document_date: "2025-09-20T00:00:00",
});

const po = record({
  id: "artifact:po",
  label: "Purchase Order - Sample Data Services",
  document_id: "PO-C03-1123",
  type: "PURCHASE_ORDER",
  amount: "4200.00",
  available_from: "2025-09-13T00:00:00",
});
const email = record({
  id: "artifact:msg",
  label: "Email - C03 September services revised estimate",
  document_id: "MSG-0003",
  type: "EMAIL",
  amount: "4500.00",
  available_from: "2025-09-25T00:00:00",
});
const packet = record({
  id: "artifact:packet",
  label: "Month-End Close Review Packet",
  document_id: "V2SRC-0051",
  type: "DOCUMENT",
  amount: "4200.00",
  available_from: "2025-10-03T00:00:00",
});
const txn = record({
  id: "entity:txn",
  label: "Ledger line",
  type: "TRANSACTION",
  origin: "entity",
  amount: "4600.00",
});

function investigate(drawnIds: string[], precedents: Precedent[] = visibilityPrecedents()) {
  return buildInvestigation({
    node: anchor,
    nodes: [anchor, po, email, packet, txn],
    edges: [
      { source: anchor.id, target: po.id, type: "CONTRADICTS" },
      { source: anchor.id, target: email.id, type: "CONTRADICTS" },
      { source: anchor.id, target: packet.id, type: "AMBIGUOUS" },
      { source: anchor.id, target: txn.id, type: "ATTACHED" },
      { source: anchor.id, target: "missing", type: "CONTRADICTS" },
    ],
    cases: [{ id: "C03", vendor: "Sample Data Services" }],
    drawnIds,
    precedents,
    blockedIds: [],
  });
}

test("suggests visible neighbors that are not already on the fan", () => {
  const { suggestions } = investigate([anchor.id, po.id]);
  assert.deepEqual(
    suggestions.map((item) => item.id),
    [email.id, packet.id]
  );
  assert.equal(suggestions.every((item) => item.id !== anchor.id), true);
  assert.equal(suggestions.some((item) => item.id === txn.id), false);
});

test("still suggests real neighbors when the fan already contains the neighborhood", () => {
  const { suggestions } = investigate([anchor.id, po.id, email.id, packet.id, txn.id]);
  assert.deepEqual(
    suggestions.map((item) => item.id),
    [po.id, email.id, packet.id]
  );
  assert.equal(suggestions.length <= 3, true);
});

test("a deprecated snapshot rule still yields no suggestions", () => {
  const precedents = visibilityPrecedents().map((item) =>
    item.id === "visibility:snapshot" ? { ...item, kept: false } : item
  );
  const { suggestions } = investigate([], precedents);
  assert.deepEqual(suggestions, []);
});
