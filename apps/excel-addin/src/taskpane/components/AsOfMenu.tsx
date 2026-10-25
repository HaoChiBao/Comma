import * as React from "react";
import { AS_OF_PRESETS, asOfPreset } from "../sidebarView";

export function AsOfMenu({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = React.useState(false);
  const current = asOfPreset(value);
  return (
    <>
      <button type="button" className="ws-sec asof-button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="mute">As of</span>
        <span className="strong">{current.label}</span>
        <span className="mute caret">▾</span>
      </button>
      {open ? (
        <div className="asof-menu" role="menu">
          {AS_OF_PRESETS.map((preset) => (
            <button
              key={preset.value}
              type="button"
              role="menuitemradio"
              aria-checked={preset.value === value}
              className={`ws-row asof-item ${preset.value === value ? "on" : ""}`}
              onClick={() => {
                setOpen(false);
                if (preset.value !== value) onChange(preset.value);
              }}
            >
              <span>{preset.label}</span>
              <span className="mute small">{preset.note}</span>
            </button>
          ))}
          <p className="asof-note">Documents received after this date are hidden, so you see only what the preparer could have known.</p>
        </div>
      ) : null}
    </>
  );
}
