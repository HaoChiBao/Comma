import * as React from "react";
import type { SelectionSummary } from "../office/selection";
import type { ScanFinding } from "../types";
import { flaggedRows } from "../sidebarView";
import { StatusDot } from "./StatusDot";

export function IdleView({
  selection,
  asOfShort,
  flagged,
  scanNote,
  error,
  onInspect,
  onPick,
}: {
  selection: SelectionSummary | null;
  asOfShort: string;
  flagged: ScanFinding[] | null;
  scanNote: string | null;
  error: string | null;
  onInspect: () => void;
  onPick: (key: string) => void;
}) {
  const single = selection?.single ? selection : null;
  return (
    <div className="idle">
      <div className="stack-4">
        <span className="mute small">Selected in Excel</span>
        {selection === null ? (
          <span className="big-ref">Reading selection…</span>
        ) : selection.single ? (
          <>
            <span className="big-ref">{selection.cell.key}</span>
            <span className="mute">{[selection.vendor, selection.cell.displayText].filter(Boolean).join(" · ") || "Empty cell"}</span>
          </>
        ) : (
          <>
            <span className="big-ref">
              {selection.sheetName}!{selection.address}
            </span>
            <span className="mute">Select one cell</span>
          </>
        )}
      </div>
      <button type="button" className="ws-prim inspect" onClick={onInspect} disabled={!single}>
        {single ? `Inspect ${single.cell.address}` : "Inspect"}
      </button>
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
      <p className="lede">Comma rebuilds what was knowable on {asOfShort}, finds every document behind this number and checks it.</p>
      <div className="flagged">
        <span className="section-label">Flagged on this sheet</span>
        {scanNote ? <p className="mute small flagged-note">{scanNote}</p> : null}
        {flagged && !scanNote && !flaggedRows(flagged).length ? <p className="mute small flagged-note">Nothing flagged.</p> : null}
        {(flagged ? flaggedRows(flagged) : []).map((row) => (
          <button key={row.cell} type="button" className="ws-row flagged-row" onClick={() => onPick(row.cell)}>
            <StatusDot color={row.view.color} fill={row.view.fill} dash={row.view.dash} />
            <span className="mute small">{row.address}</span>
            <span className="ellipsis">{row.vendor_name}</span>
            <span className="small" style={{ color: row.view.color }}>
              {row.view.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
