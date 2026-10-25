import * as React from "react";
import type { Trace } from "../types";
import { STATUS_VIEW, formatMoney } from "../view";

export function FindingCard({ trace }: { trace: Trace }) {
  const view = STATUS_VIEW[trace.status];
  return (
    <section className={`card finding ${view.tone}`} aria-label="Finding">
      <div className="finding-head">
        <span className={`badge ${view.tone}`}>{view.label}</span>
        <strong>{trace.title}</strong>
      </div>
      <p>{trace.summary}</p>
      {trace.difference ? <p className="muted">Difference {formatMoney(trace.difference.amount, trace.difference.currency)}</p> : null}
      {trace.unknowns.length ? (
        <ul className="unknowns">
          {trace.unknowns.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
