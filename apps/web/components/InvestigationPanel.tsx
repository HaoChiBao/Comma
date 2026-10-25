"use client";

import { useEffect, useRef } from "react";
import type { AgentSuggestion, InvestigationStep, Precedent } from "@/lib/investigate";

export function InvestigationPanel({
  steps,
  shown,
  thinking,
  precedentsOpen,
  precedents,
  suggestion,
  onApprove,
  onDismiss,
  onTogglePrecedent,
}: {
  steps: InvestigationStep[];
  shown: number;
  thinking?: boolean;
  precedentsOpen: boolean;
  precedents: Precedent[];
  suggestion: AgentSuggestion | null;
  onApprove: (id: string) => void;
  onDismiss: (id: string) => void;
  onTogglePrecedent: (id: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const learned = precedents.filter((item) => item.kind === "learned");
  const visibility = precedents.filter((item) => item.kind === "visibility");
  const visibleSteps = steps.slice(0, shown);

  useEffect(() => {
    if (!thinking && shown === 0) return;
    const node = rootRef.current;
    if (!node) return;
    const scroller = node.closest(".floating-inspector");
    if (!(scroller instanceof HTMLElement)) return;

    const align = () => {
      const previous = node.previousElementSibling;
      const anchor =
        previous instanceof HTMLElement && previous.classList.contains("inspector-agent-row") ? previous : node;
      const scrollerRect = scroller.getBoundingClientRect();
      const rect = anchor.getBoundingClientRect();
      if (rect.top < scrollerRect.top - 40) return;
      if (rect.top <= scrollerRect.top + 16 && node.getBoundingClientRect().top < scrollerRect.bottom - 24) return;
      const delta = rect.top - scrollerRect.top;
      scroller.scrollTo({ top: Math.max(0, scroller.scrollTop + delta - 8), behavior: "auto" });
    };

    align();
    const observer = new ResizeObserver(() => align());
    observer.observe(scroller);
    const stop = window.setTimeout(() => observer.disconnect(), thinking ? 16000 : 1500);
    return () => {
      observer.disconnect();
      window.clearTimeout(stop);
    };
  }, [thinking, shown]);

  return (
    <div className="investigation-panel" ref={rootRef}>
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

      {thinking ? (
        <div className="agent-thinking" role="status" aria-live="polite" aria-label="Thinking">
          <span className="agent-review-kind">Thinking</span>
          <p>Reading the records already visible.</p>
          <span className="agent-thinking-line" />
          <span className="agent-thinking-line short" />
        </div>
      ) : null}

      {visibleSteps.length > 0 ? (
        <ol className="agent-review" role="log" aria-live="polite" aria-label="Investigation">
          {visibleSteps.map((step, index) => (
            <li key={`${step.kind}-${index}`}>
              <span className="agent-review-kind">{step.kind}</span>
              <p>{step.text}</p>
              {step.lines && step.lines.length > 0 ? (
                <ul>
                  {step.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
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
