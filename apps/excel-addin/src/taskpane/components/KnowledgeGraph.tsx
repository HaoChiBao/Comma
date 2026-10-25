import * as React from "react";
import type { SidebarTrace } from "../types";
import { GRAPH_H, GRAPH_W, layoutGraph } from "../sidebarView";

export function KnowledgeGraph({ trace, selected, onToggle }: { trace: SidebarTrace; selected: string; onToggle: (id: string) => void }) {
  const { nodes, edges } = layoutGraph(trace, selected);
  return (
    <svg
      viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
      role="img"
      aria-label={`${trace.cell.address} in the middle, formula inputs on the left, documents on the right. Click a circle for details.`}
      className="graph"
    >
      {edges.map((edge) => (
        <path key={edge.id} d={edge.d} fill="none" stroke={edge.stroke} strokeWidth={edge.width} strokeDasharray={edge.dash} strokeLinecap="round" opacity={edge.opacity} />
      ))}
      {nodes.map((node) => (
        <g
          key={node.id}
          className={node.clickable ? "ws-nd" : undefined}
          style={{ ["--tint" as string]: node.tint }}
          onClick={node.clickable ? () => onToggle(node.id) : undefined}
          role={node.clickable ? "button" : undefined}
          tabIndex={node.clickable ? 0 : undefined}
          aria-label={node.clickable ? `${node.id}, show details` : undefined}
          onKeyDown={node.clickable ? (event) => (event.key === "Enter" || event.key === " " ? onToggle(node.id) : undefined) : undefined}
        >
          <circle cx={node.x} cy={node.y} r={node.hit} fill="transparent" />
          <circle className="ws-ring" cx={node.x} cy={node.y} r={node.ring} fill="none" stroke={node.stroke} strokeWidth="1" opacity={node.ringOpacity} />
          <circle className="ws-vis" cx={node.x} cy={node.y} r={node.r} fill={node.fill} stroke={node.stroke} strokeWidth={node.strokeWidth} strokeDasharray={node.dash} opacity={node.opacity} />
          <text className="ws-l1" x={node.x} y={node.y} textAnchor="middle" dominantBaseline="central" fontSize={node.labelSize} fontWeight={700} fill={node.labelColor} pointerEvents="none">
            {node.label}
          </text>
        </g>
      ))}
    </svg>
  );
}
