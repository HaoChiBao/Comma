import * as React from "react";
import type { Trace } from "../types";
import { REASON_LABEL, RELATIONSHIP_LABEL, comparisonLabel, evidenceView, formatDate, formatMoney } from "../view";

export function EvidenceList({ trace, onOpenSource }: { trace: Trace; onOpenSource: (sourceId: string) => void }) {
  const { available, excluded } = evidenceView(trace);
  return (
    <section className="card" aria-label="Evidence">
      <h3>Evidence</h3>
      {!available.length && !excluded.length ? <p className="muted">No linked sources.</p> : null}
      {available.map((item) => (
        <button key={`${item.cell}-${item.source_id}`} className={`evidence ${item.relationship === "CONFLICTS_WITH" ? "conflict" : ""}`} onClick={() => onOpenSource(item.source_id)}>
          <div className="evidence-head">
            <strong>{item.external_id}</strong>
            <span className="amount">{formatMoney(item.amount, item.currency)}</span>
          </div>
          <div className="muted">
            {item.kind.replace("_", " ")} · received {formatDate(item.available_at)} · {item.locator}
          </div>
          {item.ledger_locator ? <div className="muted mono">ledger {item.ledger_locator}</div> : null}
          <div className="muted">{item.basis}</div>
          <div className="tags">
            <span className={`tag ${item.relationship.toLowerCase()}`}>{RELATIONSHIP_LABEL[item.relationship]}</span>
            <span className={`tag ${item.link_status}`}>{item.link_status === "verified" ? "verified link" : "candidate link"}</span>
            <span className="tag">{comparisonLabel(item)}</span>
            {item.cell !== trace.cell.key ? <span className="tag">via {item.cell.split("!")[1]}</span> : null}
          </div>
        </button>
      ))}
      {excluded.map((item) => (
        <div key={`${item.cell}-${item.source_id}`} className="evidence blocked" aria-label="Withheld source">
          <div className="evidence-head">
            <strong>Withheld source</strong>
            <span className="amount">—</span>
          </div>
          <div className="muted">{REASON_LABEL[item.reason] ?? item.reason}. Contents are not shown for this date.</div>
        </div>
      ))}
    </section>
  );
}
