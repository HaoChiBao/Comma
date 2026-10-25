import * as React from "react";
import type { GraphNode, TraceGraph } from "../types";
import { NODE_H, NODE_W, RELATIONSHIP_LABEL, edgeStyle, formatMoney, layoutGraph } from "../view";

export function LineageGraph({ graph, onSelectNode }: { graph: TraceGraph; onSelectNode: (node: GraphNode) => void }) {
  const width = 320;
  const { nodes, edges, height } = layoutGraph(graph, width);
  const placed = new Map(nodes.map((node) => [node.id, node]));
  return (
    <section className="card" aria-label="Provenance graph">
      <h3>Trace</h3>
      <svg className="graph" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cell, precedents, and sources">
        {edges.map((edge) => {
          const from = placed.get(edge.source);
          const to = placed.get(edge.target);
          if (!from || !to) return null;
          const style = edgeStyle(edge);
          const forward = from.x <= to.x;
          const x1 = forward ? from.x + NODE_W : from.x;
          const x2 = forward ? to.x : to.x + NODE_W;
          const y1 = from.y + NODE_H / 2;
          const y2 = to.y + NODE_H / 2;
          const mid = (x1 + x2) / 2;
          return (
            <path
              key={edge.id}
              d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
              fill="none"
              stroke={style.stroke}
              strokeDasharray={style.dash}
              strokeWidth={1.5}
            >
              <title>
                {RELATIONSHIP_LABEL[edge.type] ?? edge.type} · {edge.verified ? "verified" : "inferred"}
                {edge.locator ? ` · ${edge.locator}` : ""}
              </title>
            </path>
          );
        })}
        {nodes.map((node) => (
          <g
            key={node.id}
            className={`node ${node.type.toLowerCase()} ${node.selected ? "selected" : ""} ${node.status ?? ""}`}
            transform={`translate(${node.x},${node.y})`}
            onClick={() => onSelectNode(node)}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => (event.key === "Enter" ? onSelectNode(node) : undefined)}
          >
            <title>{node.title}</title>
            <rect width={NODE_W} height={NODE_H} rx={6} />
            <text x={8} y={16} className="node-label">
              {node.label}
            </text>
            <text x={8} y={31} className="node-sub">
              {node.type === "Blocked" ? "withheld" : node.amount ? formatMoney(node.amount) : node.type}
            </text>
          </g>
        ))}
      </svg>
      <div className="legend">
        {(["FORMULA_DEPENDS_ON", "SUPPORTED_BY", "CONFLICTS_WITH", "POSSIBLY_RELATED_TO", "EXCLUDED"] as const).map((type) => {
          const style = edgeStyle({ id: type, source: "", target: "", type, verified: true, inferred: false });
          return (
            <span key={type}>
              <svg width="18" height="6" aria-hidden="true">
                <line x1="0" y1="3" x2="18" y2="3" stroke={style.stroke} strokeDasharray={style.dash} strokeWidth="2" />
              </svg>
              {RELATIONSHIP_LABEL[type]}
            </span>
          );
        })}
      </div>
    </section>
  );
}
