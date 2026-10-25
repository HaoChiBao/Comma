"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Point = { x: number; y: number };

export function EvidenceGraph({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 720, h: 560 });
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => setSize({ w: element.clientWidth, h: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const signature = nodes.map((node) => node.id).join("|");
  const initial = useMemo(() => layout(nodes, size), [signature, size.w, size.h]);
  const [positions, setPositions] = useState<Record<string, Point>>(initial);
  useEffect(() => setPositions(initial), [initial]);
  const placed = { ...initial, ...positions };

  return (
    <div className="graph" ref={frame} role="group" aria-label="Evidence graph">
      <svg aria-hidden="true">
        {edges.map((edge) => {
          const from = placed[edge.source];
          const to = placed[edge.target];
          if (!from || !to) return null;
          return (
            <line
              key={edge.id}
              x1={from.x + 70}
              y1={from.y + 28}
              x2={to.x + 70}
              y2={to.y + 28}
              stroke={edge.type === "DERIVED_FROM" ? "#7fd3ff" : edge.inferred ? "#e6b35a" : "#5d7388"}
              strokeDasharray={edge.inferred ? "4 4" : undefined}
              strokeWidth="1.4"
            />
          );
        })}
      </svg>
      {nodes.map((node) => {
        const point = placed[node.id] || { x: 40, y: 40 };
        return (
          <button
            key={node.id}
            className={`node${node.redacted ? " redacted" : ""}${selected === node.id ? " selected" : ""}`}
            style={{ left: point.x, top: point.y }}
            onClick={() => onSelect(node.id)}
            onPointerDown={(event) => {
              const startX = event.clientX;
              const startY = event.clientY;
              const origin = point;
              const move = (next: PointerEvent) => {
                setPositions((current) => ({
                  ...initial,
                  ...current,
                  [node.id]: { x: origin.x + next.clientX - startX, y: origin.y + next.clientY - startY },
                }));
              };
              const up = () => {
                window.removeEventListener("pointermove", move);
                window.removeEventListener("pointerup", up);
              };
              window.addEventListener("pointermove", move);
              window.addEventListener("pointerup", up);
            }}
          >
            <small>{node.redacted ? "Unavailable" : node.type}</small>
            <strong>{node.redacted ? "Blocked evidence" : node.amount ? `$${node.amount}` : node.label}</strong>
            <small>{node.redacted ? node.available_from?.slice(0, 10) : node.label}</small>
          </button>
        );
      })}
    </div>
  );
}

function layout(nodes: GraphNode[], size: { w: number; h: number }): Record<string, Point> {
  const points: Record<string, Point> = {};
  const radius = Math.max(90, Math.min(size.w, size.h) * 0.32);
  nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    points[node.id] = {
      x: size.w / 2 - 70 + Math.cos(angle) * radius,
      y: size.h / 2 - 28 + Math.sin(angle) * radius * 0.72,
    };
  });
  return points;
}
