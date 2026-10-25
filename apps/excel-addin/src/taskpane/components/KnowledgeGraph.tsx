import * as React from "react";
import type { SidebarTrace } from "../types";
import { GRAPH_H, GRAPH_W, layoutGraph } from "../sidebarView";

export function KnowledgeGraph({ trace, selected, onToggle }: { trace: SidebarTrace; selected: string; onToggle: (id: string) => void }) {
  const { nodes, edges, height } = layoutGraph(trace, selected);
  return (
    <svg
      viewBox={`0 0 ${GRAPH_W} ${height}`}
      role="img"
      aria-label={`${trace.cell.address} in the middle, formula inputs on the left, documents on the right. Click a rectangle for details.`}
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
          aria-label={node.clickable ? `${node.label}${node.infoLabel ? `, ${node.infoLabel}` : ""}, show details` : undefined}
          onKeyDown={node.clickable ? (event) => (event.key === "Enter" || event.key === " " ? onToggle(node.id) : undefined) : undefined}
        >
          <rect x={node.x - node.width / 2 - 6} y={node.y - node.height / 2 - 5} width={node.width + 12} height={node.height + 10} rx="9" fill="transparent" />
          <rect className="ws-ring" x={node.x - node.width / 2 - 4} y={node.y - node.height / 2 - 4} width={node.width + 8} height={node.height + 8} rx="8" fill="none" stroke={node.stroke} strokeWidth="1" opacity={node.ringOpacity} />
          <rect className="ws-vis" x={node.x - node.width / 2} y={node.y - node.height / 2} width={node.width} height={node.height} rx="6" fill={node.fill} stroke={node.stroke} strokeWidth={node.strokeWidth} strokeDasharray={node.dash} opacity={node.opacity} />
          <text className="ws-l1" x={node.infoLabel ? node.x - node.width / 2 + 10 : node.x} y={node.y} textAnchor={node.infoLabel ? "start" : "middle"} dominantBaseline="central" fontSize={node.labelSize} fontWeight={700} fill={node.labelColor} pointerEvents="none">
            {node.label}
          </text>
          {node.infoLabel ? <text className="ws-l2" x={node.x + node.width / 2 - 10} y={node.y} textAnchor="end" dominantBaseline="central" fontSize={node.infoLabel.length > 6 ? 9 : 10.5} fontWeight={600} fill={node.labelColor} pointerEvents="none">{node.infoLabel}</text> : null}
        </g>
      ))}
    </svg>
  );
}
