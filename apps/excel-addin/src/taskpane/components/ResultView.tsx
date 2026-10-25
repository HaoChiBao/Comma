import * as React from "react";
import type { SidebarTrace } from "../types";
import { ACTIONS, STATUS_VIEW, countLabel, itemIcon, money, nothingLinked, timelineRows } from "../sidebarView";
import { formatMoney, originLine } from "../view";
import { KnowledgeGraph } from "./KnowledgeGraph";
import { StatusDot } from "./StatusDot";

export function ResultView({ trace, vendor, selected, onSelect, onOpenSource, onAddNote, onAction, busy }: { trace: SidebarTrace; vendor: string; selected: string; onSelect: (id: string) => void; onOpenSource: (id: string) => void; onAddNote: (message: string) => Promise<boolean>; onAction: (label: string) => void; busy: boolean }) {
  const [graphOpen, setGraphOpen] = React.useState(true);
  const [noteOpen, setNoteOpen] = React.useState(false);
  const [noteDraft, setNoteDraft] = React.useState("");
  const status = STATUS_VIEW[trace.status];
  const value = typeof trace.cell.raw_value === "number" ? money(trace.cell.raw_value) : trace.cell.display_text || String(trace.cell.raw_value ?? "");
  const item = trace.items.find((entry) => entry.id === selected);
  const toggle = (id: string) => onSelect(selected === id ? "" : id);
  const vendorName = trace.cell.vendor_name || vendor;
  const submitNote = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!noteDraft.trim() || busy) return;
    if (await onAddNote(noteDraft)) {
      setNoteDraft("");
      setNoteOpen(false);
    }
  };
  return (
    <div>
      <section className="summary">
        <span className="mute small">
          {trace.cell.key}
          {vendorName ? ` · ${vendorName}` : ""}
        </span>
        <span className="value">{value}</span>
        <span className="status-line" style={{ color: status.color }}>
          <StatusDot color={status.color} fill={status.fill} dash={status.dash} />
          {status.label}
        </span>
        <strong className="result-title">{trace.title}</strong>
        <p className="headline">{trace.headline}</p>
        {trace.difference ? <span className="mute small">Difference {formatMoney(trace.difference.amount, trace.difference.currency)}</span> : null}
        {originLine(trace.origin) ? <span className="mute small">{originLine(trace.origin)}</span> : null}
        {trace.unknowns.length ? <details className="unknown-detail"><summary>{trace.unknowns.length} limitation{trace.unknowns.length === 1 ? "" : "s"}</summary><ul>{trace.unknowns.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
        <div className="formula-box">
          <span className="mute small">Formula</span>
          <span className="mono small">{trace.formula_display || "—"}</span>
          <span className="mute small ellipsis">{trace.calc}</span>
        </div>
      </section>

      <section className="graph-section">
        <div className="row-between">
          <button type="button" className="ws-quiet graph-toggle" onClick={() => setGraphOpen(!graphOpen)} aria-expanded={graphOpen}>
            <span className="chev">{graphOpen ? "▾" : "▸"}</span>Knowledge graph <span className="normal">· {countLabel(trace.items)}</span>
          </button>
          {/* Not wired to anything yet. */}
          <button type="button" className="ws-sec small-button">
            Open full graph
          </button>
        </div>
        {graphOpen ? <KnowledgeGraph trace={trace} selected={selected} onToggle={toggle} /> : null}
        <div className="graph-detail">
        {item ? (
            <div className="selected-card">
              <div className="selected-head">
                <StatusDot color={itemIcon(item).color} fill={itemIcon(item).fill} dash={itemIcon(item).dash} />
                <span>
                  <span className="strong">{item.id === "later" ? "Later document" : item.id}</span> <span className="mute">{item.kind === "withheld" ? "withheld" : item.kind}</span>
                </span>
                <span className="strong tabular">{money(item.amount, item.currency)}</span>
              </div>
              <span className="small strong" style={{ color: itemIcon(item).textColor }}>
                {item.relationship_text}
              </span>
              <div className="details">
                {item.details.map((row) => (
                  <React.Fragment key={row.k}>
                    <span className="mute">{row.k}</span>
                    <span>{row.v}</span>
                  </React.Fragment>
                ))}
              </div>
              {item.source_id ? <button type="button" className="ws-link" onClick={() => onOpenSource(item.source_id!)} disabled={busy}>Open source</button> : null}
            </div>
          ) : trace.items.length ? (
            <p className="hint">Click a rectangle to see what it is and how it connects to {trace.cell.address}.</p>
          ) : null}
          {nothingLinked(trace.items) ? <p className="mute none-linked">Nothing in the ERP, inbox or drive points to this number.</p> : null}
        </div>
      </section>

      <section className="trace-section">
        <span className="section-label">Trace</span>
        <div className="timeline">
          {timelineRows(trace.timeline).map((row, index) => (
            <div key={`${index}-${row.text}`} className="event" style={{ opacity: row.opacity }}>
              <span className="mute small tabular">{row.date}</span>
              <span className="event-rail">
                <span className="event-line" style={{ borderLeftColor: row.line }} />
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className="event-dot">
                  <circle cx="6" cy="6" r={row.r} fill={row.fill} stroke={row.stroke} strokeWidth="1.5" strokeDasharray={row.dash} />
                </svg>
              </span>
              <span className="event-text" style={{ fontWeight: row.weight, color: row.textColor }}>
                {row.text}
              </span>
              <span className="mute small tabular">{row.amount}</span>
            </div>
          ))}
        </div>
      </section>
      <div className="result-actions">
        {ACTIONS[trace.status].map((action) => <button key={action.label} type="button" className={action.primary ? "ws-prim action" : "ws-sec action"} onClick={() => onAction(action.label)}>{action.label}</button>)}
        <button type="button" className="ws-sec action" onClick={() => setNoteOpen((open) => !open)} aria-expanded={noteOpen} aria-controls="custom-note-composer" disabled={busy}>Add a note</button>
      </div>
      {noteOpen ? (
        <form id="custom-note-composer" className="note-composer" onSubmit={(event) => void submitNote(event)}>
          <label htmlFor="custom-note-message">Note for {trace.cell.address}</label>
          <p className="mute small">This appears in Excel’s native note popup. Existing notes not created by WorldState are left unchanged.</p>
          <textarea id="custom-note-message" value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder="Write your note…" rows={4} maxLength={30000} autoFocus />
          <div className="note-composer-actions">
            <button type="submit" className="ws-prim action" disabled={busy || !noteDraft.trim()}>Add note</button>
            <button type="button" className="ws-sec action" onClick={() => { setNoteOpen(false); setNoteDraft(""); }} disabled={busy}>Cancel</button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
