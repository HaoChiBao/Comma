import * as React from "react";
import type { ScanResult } from "../types";
import { STATUS_VIEW } from "../view";

export function ScanResults({ result, onSelectCell }: { result: ScanResult; onSelectCell: (key: string) => void }) {
  const flagged = result.findings.filter((item) => item.status !== "supported");
  return (
    <section className="card" aria-label="Scan results">
      <h3>Scan</h3>
      <p className="muted">
        {result.scanned} cells read · {result.counts.review ?? 0} review · {result.counts.unknown ?? 0} unknown · {result.counts.supported ?? 0} supported
      </p>
      {flagged.length ? null : <p>No cells need review.</p>}
      {flagged.map((item) => (
        <button key={item.cell} className="evidence" onClick={() => onSelectCell(item.cell)}>
          <div className="evidence-head">
            <strong>{item.address}</strong>
            <span className={`badge ${STATUS_VIEW[item.status].tone}`}>{STATUS_VIEW[item.status].label}</span>
          </div>
          <div>{item.title}</div>
          <div className="muted">{item.summary}</div>
        </button>
      ))}
    </section>
  );
}
