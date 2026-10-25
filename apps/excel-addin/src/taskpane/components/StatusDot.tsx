import * as React from "react";

export function StatusDot({ color, fill, dash, r = 4.5 }: { color: string; fill: string; dash: string; r?: number }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r={r} fill={fill} stroke={color} strokeWidth="1.5" strokeDasharray={dash} />
    </svg>
  );
}
