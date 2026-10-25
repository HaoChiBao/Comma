import * as React from "react";
import type { SourceDetail } from "../types";
import { formatDate, formatMoney } from "../view";

export function SourcePanel({ source, onClose }: { source: SourceDetail; onClose: () => void }) {
  return (
    <section className="card source" aria-label="Source document">
      <div className="evidence-head">
        <strong>{source.title}</strong>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="muted">
        {source.kind.replace("_", " ")} · dated {formatDate(source.document_date)} · received {formatDate(source.available_at)}
      </div>
      <pre className="source-text">{source.content_text}</pre>
      <ul className="facts">
        {source.facts.map((fact) => (
          <li key={fact.fact_id}>
            {fact.locator}: {formatMoney(fact.amount, fact.currency)}
          </li>
        ))}
      </ul>
      {source.availability_evidence ? <p className="muted">Availability: {source.availability_evidence}</p> : null}
      <p className="muted mono">sha256 {source.content_hash.slice(0, 16)}…</p>
    </section>
  );
}
