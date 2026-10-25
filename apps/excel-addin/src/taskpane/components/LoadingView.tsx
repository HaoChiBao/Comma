import * as React from "react";
import { DOT_GRID, LOADING_STEPS } from "../sidebarView";

export function LoadingView({ cellKey, address, asOfShort, step }: { cellKey: string; address: string; asOfShort: string; step: number }) {
  return (
    <div className="idle">
      <div className="stack-4">
        <span className="mute small">Inspecting</span>
        <span className="big-ref">{cellKey}</span>
      </div>
      <svg viewBox="0 0 328 112" aria-hidden="true" className="dots">
        {DOT_GRID.map((dot) => (
          <circle key={`${dot.x}-${dot.y}`} className="ws-dot" cx={dot.x} cy={dot.y} r="1.6" fill="#7A1515" style={{ animationDelay: `${dot.delay}s` }} />
        ))}
      </svg>
      <div className="steps" role="status" aria-live="polite">
        {LOADING_STEPS(address, asOfShort).map((text, index) => (
          <div key={text} className="step" style={{ color: index <= step ? "#1F1A17" : "#A39A92" }}>
            {index < step ? (
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <circle cx="7" cy="7" r="6" fill="#2E6B52" />
                <path d="M4 7.2l2 2 4-4.2" fill="none" stroke="#FFFFFF" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            ) : index === step ? (
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <circle className="ws-pulse" cx="7" cy="7" r="5.25" fill="none" stroke="#7A1515" strokeWidth="1.5" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <circle cx="7" cy="7" r="5.25" fill="none" stroke="#D6CEC5" strokeWidth="1.5" strokeDasharray="1.5 2" />
              </svg>
            )}
            <span>{text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
