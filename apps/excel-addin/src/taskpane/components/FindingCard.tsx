import * as React from "react";
import type { Trace } from "../types";
import { STATUS_VIEW, formatMoney, originLine } from "../view";

export function FindingCard({ trace, onAddNote, busy }: { trace: Trace; onAddNote?: () => void; busy?: boolean }) {
  const view = STATUS_VIEW[trace.status];
  const origin = originLine(trace.origin);
  return (
    <section className={`card finding ${view.tone}`} aria-label="Finding">
      <div className="finding-head">
        <span className={`badge ${view.tone}`}>{view.label}</span>
        <strong>{trace.title}</strong>
        {trace.case_id ? <span className="muted">{trace.case_id}</span> : null}
      </div>
      <p>{trace.summary}</p>
      {trace.difference ? <p className="muted">Difference {formatMoney(trace.difference.amount, trace.difference.currency)}</p> : null}
      {origin ? <p className="origin">{origin}</p> : null}
      {trace.unknowns.length ? (
        <ul className="unknowns">
          {trace.unknowns.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {onAddNote ? (
        <button className="link" onClick={onAddNote} disabled={busy}>
          Add note to {trace.cell.address}
        </button>
      ) : null}
    </section>
  );
}
