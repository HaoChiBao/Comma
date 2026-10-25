import * as React from "react";
import type { Trace } from "../types";
import { formatMoney } from "../view";

export function CellCard({ trace, precedentValues, precedentsSupported, onSelectCell }: {
  trace: Trace;
  precedentValues: Record<string, unknown>;
  precedentsSupported: boolean;
  onSelectCell: (key: string) => void;
}) {
  const { cell } = trace;
  const numeric = typeof cell.raw_value === "number";
  return (
    <section className="card" aria-label="Selected cell">
      <div className="cell-head">
        <span className="cell-key">{cell.key}</span>
        {cell.period ? <span className="muted">period {cell.period}</span> : null}
      </div>
      <div className="cell-value">{cell.display_text || (numeric ? formatMoney(cell.raw_value as number) : String(cell.raw_value ?? ""))}</div>
      {cell.vendor_name ? (
        <div className="muted">
          {cell.vendor_name}
          {cell.reference ? ` · Ref ${cell.reference}` : ""}
        </div>
      ) : null}
      <h3>Lineage</h3>
      {cell.formula ? (
        <>
          <code className="formula">{cell.formula}</code>
          {trace.lineage.length ? (
            <p className="lineage">
              {cell.address} ={" "}
              {trace.lineage.map((edge, index) => (
                <React.Fragment key={edge.from}>
                  {index ? " + " : ""}
                  <button className="link" onClick={() => onSelectCell(edge.from)}>
                    {edge.from.split("!")[1]}
                  </button>{" "}
                  <span className="muted">({formatValue(precedentValues[edge.from])})</span>
                </React.Fragment>
              ))}
            </p>
          ) : (
            <p className="muted">
              {precedentsSupported
                ? "No direct precedents found."
                : "This Excel host does not support precedent lookup (needs ExcelApi 1.12). Showing formula text only."}
            </p>
          )}
        </>
      ) : (
        <p className="muted">No formula. This is a manually entered value.</p>
      )}
    </section>
  );
}

function formatValue(value: unknown): string {
  return typeof value === "number" ? formatMoney(value) : String(value ?? "—");
}
