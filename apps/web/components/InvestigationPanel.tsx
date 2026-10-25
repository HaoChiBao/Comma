"use client";

import type { AgentSuggestion, InvestigationStep, Precedent } from "@/lib/investigate";

export function InvestigationPanel({
  steps,
  shown,
  precedentsOpen,
  precedents,
  suggestion,
  onApprove,
  onDismiss,
  onTogglePrecedent,
}: {
  steps: InvestigationStep[];
  shown: number;
  precedentsOpen: boolean;
  precedents: Precedent[];
  suggestion: AgentSuggestion | null;
  onApprove: (id: string) => void;
  onDismiss: (id: string) => void;
  onTogglePrecedent: (id: string) => void;
}) {
  const learned = precedents.filter((item) => item.kind === "learned");
  const visibility = precedents.filter((item) => item.kind === "visibility");
  const visibleSteps = steps.slice(0, shown);

  return (
    <div className="investigation-panel">
      {suggestion ? (
        <div className="suggestion-card">
          <div className="suggestion-kicker">Suggested by Comma Agent</div>
          <div className="suggestion-name">
            {suggestion.label}
            {suggestion.amount ? ` · $${suggestion.amount}` : ""}
          </div>
          <p>{suggestion.reason}.</p>
          <div className="suggestion-actions">
            <button type="button" className="primary" onClick={() => onApprove(suggestion.id)}>
              Approve
            </button>
            <button type="button" onClick={() => onDismiss(suggestion.id)}>
              Dismiss
            </button>
          </div>
        </div>
      ) : null}

      {visibleSteps.length > 0 ? (
        <div className="agent-terminal" role="log" aria-live="polite" aria-label="Investigation">
          {visibleSteps.map((step, index) => (
            <p key={`${step.kind}-${index}`}>
              <span className="kind">[{step.kind}]</span> {step.text}
            </p>
          ))}
        </div>
      ) : null}

      {precedentsOpen ? (
        <section className="precedent-panel" aria-label="Learned precedents">
          <h2>Learned precedents</h2>
          {learned.length > 0 ? (
            <ul>
              {learned.map((item) => (
                <PrecedentRow key={item.id} item={item} onToggle={onTogglePrecedent} />
              ))}
            </ul>
          ) : null}
          <ul>
            {visibility.map((item) => (
              <PrecedentRow key={item.id} item={item} onToggle={onTogglePrecedent} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function PrecedentRow({ item, onToggle }: { item: Precedent; onToggle: (id: string) => void }) {
  return (
    <li className={item.kept ? "precedent-row" : "precedent-row deprecated"}>
      <p>{item.rule}</p>
      {item.recordIds.length > 0 ? <div className="precedent-ids">{item.recordIds.join(" · ")}</div> : null}
      <button type="button" aria-pressed={item.kept} onClick={() => onToggle(item.id)}>
        {item.kept ? "Deprecate" : "Keep"}
      </button>
    </li>
  );
}
