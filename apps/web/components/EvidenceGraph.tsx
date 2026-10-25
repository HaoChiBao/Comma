"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Point = { x: number; y: number };
type Label = { id: string; x: number; y: number };

export function EvidenceGraph({
  nodes,
  edges,
  selected,
  focused,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  focused?: string | null;
  onSelect: (id: string) => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 720, h: 560 });
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => setSize({ w: element.clientWidth, h: Math.max(element.clientHeight, 520) });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const signature = `${nodes.length}:${nodes.map((node) => node.group || "").join("|")}`;
  const arranged = useMemo(() => layout(nodes, size), [signature, size.w, size.h]);
  const compact = nodes.length > 30;
  const [positions, setPositions] = useState<Record<string, Point>>(arranged.points);
  useEffect(() => setPositions(arranged.points), [arranged]);
  const placed = { ...arranged.points, ...positions };

  const boardWidth = Math.max(arranged.w, size.w);
  const boardHeight = Math.max(arranged.h, size.h);
  const ox = compact ? 42 : 70;
  const oy = compact ? 16 : 28;
  return (
    <div className={`graph${compact ? " compact" : ""}`} ref={frame} role="group" aria-label="Evidence graph">
      <div className="graph-board" style={{ width: boardWidth, height: boardHeight }}>
      <svg aria-hidden="true" width={boardWidth} height={boardHeight}>
        {edges.map((edge) => {
          const from = placed[edge.source];
          const to = placed[edge.target];
          if (!from || !to) return null;
          return (
            <line
              key={edge.id}
              x1={from.x + ox}
              y1={from.y + oy}
              x2={to.x + ox}
              y2={to.y + oy}
              stroke={edgeColor(edge.type)}
              strokeDasharray={edge.inferred || edge.type === "POTENTIALLY_SUPPORTS" || edge.type === "MENTIONS" ? "4 4" : undefined}
              strokeWidth="1.4"
            />
          );
        })}
      </svg>
      {arranged.labels.map((label) => (
        <div key={label.id} className="cluster-label" data-cluster={label.id} style={{ left: label.x, top: label.y }}>
          {label.id}
        </div>
      ))}
      {nodes.map((node) => {
        const point = placed[node.id] || { x: 40, y: 40 };
        const dim = Boolean(focused && node.group && node.group !== focused && node.group !== "policy");
        return (
          <button
            key={node.id}
            className={`node${node.redacted ? " redacted" : ""}${selected === node.id ? " selected" : ""}${dim ? " dim" : ""}`}
            style={{ left: point.x, top: point.y }}
            onClick={() => onSelect(node.id)}
            onPointerDown={(event) => {
              const startX = event.clientX;
              const startY = event.clientY;
              const origin = point;
              const move = (next: PointerEvent) => {
                setPositions((current) => ({
                  ...arranged.points,
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
    </div>
  );
}

function edgeColor(type: string) {
  if (type === "CONTRADICTS") return "#e07a72";
  if (type === "RECONCILES_WITH" || type === "CORRECTS") return "#8dcea0";
  if (type === "POTENTIALLY_SUPPORTS" || type === "DERIVED_FROM" || type === "MENTIONS" || type === "SOURCE_CANDIDATE") return "#7fd3ff";
  if (type === "GOVERNS" || type === "GOVERNED_BY" || type === "SUPERSEDES" || type === "AMBIGUOUS") return "#e6b35a";
  return "#5d7388";
}

type Board = { points: Record<string, Point>; labels: Label[]; w: number; h: number };

function layout(nodes: GraphNode[], size: { w: number; h: number }): Board {
  if (nodes.length > 30) return companyBoard(nodes);
  const grouped = nodes.some((node) => node.group);
  if (!grouped) return { points: circle(nodes, size, size.w / 2, size.h / 2, Math.max(90, Math.min(size.w, size.h) * 0.32)), labels: [], w: size.w, h: size.h };
  const groups = [...new Set(nodes.map((node) => node.group || "other"))];
  const cases = groups.filter((group) => group !== "policy");
  if (cases.length === 1) {
    const radius = Math.max(150, Math.min(size.w, size.h) * 0.34);
    return {
      points: circle(nodes, size, size.w / 2, size.h / 2, radius),
      labels: [{ id: cases[0], x: size.w / 2 - 16, y: 18 }],
      w: size.w,
      h: size.h,
    };
  }
  const centers: Record<string, Point> = {};
  const cx = size.w / 2;
  const cy = size.h / 2;
  centers.policy = { x: cx, y: cy };
  const ring = Math.max(180, Math.min(size.w, size.h) * 0.34);
  cases.forEach((group, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(cases.length, 1) - Math.PI / 2;
    centers[group] = { x: cx + Math.cos(angle) * ring, y: cy + Math.sin(angle) * ring * 0.78 };
  });
  const points: Record<string, Point> = {};
  const labels: Label[] = [];
  for (const group of groups) {
    const members = nodes.filter((node) => (node.group || "other") === group);
    const center = centers[group] || { x: cx, y: cy };
    const radius = members.length <= 1 ? 0 : Math.max(48, 16 * members.length);
    members.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(members.length, 1) - Math.PI / 2;
      points[node.id] = {
        x: center.x - 70 + Math.cos(angle) * radius,
        y: center.y - 28 + Math.sin(angle) * radius * 0.86,
      };
    });
    if (group !== "policy") labels.push({ id: group, x: center.x - 18, y: center.y - radius - 46 });
  }
  return { points, labels, w: size.w, h: size.h };
}

const KIND_LABEL: Record<string, string> = {
  invoice: "Invoices",
  purchase_order: "Purchase orders",
  email: "Emails",
  contract: "Contracts",
  memo: "Confirmations",
  journal: "Entries",
  other: "Other documents",
};

function companyBoard(nodes: GraphNode[]): Board {
  const points: Record<string, Point> = {};
  const labels: Label[] = [];
  const caseGroups = [...new Set(nodes.map((node) => node.group || "").filter((group) => /^C\d+$/.test(group)))].sort();
  const policies = nodes.filter((node) => node.group === "policy");
  const otherGroups = [...new Set(nodes.map((node) => node.group || "other").filter((group) => group !== "policy" && !/^C\d+$/.test(group)))].sort();
  const colW = 98;
  const rowH = 50;
  const cellW = 310;
  const cellH = 190;
  const cols = 5;
  let y = 16;
  if (policies.length) {
    labels.push({ id: "Policies", x: 16, y });
    y += 22;
    placeGrid(policies, 16, y, colW, rowH, 8, points);
    y += Math.ceil(policies.length / 8) * rowH + 24;
  }
  caseGroups.forEach((group, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    const x = 16 + col * cellW;
    const top = y + row * cellH;
    labels.push({ id: group, x, y: top });
    placeGrid(nodes.filter((node) => node.group === group), x, top + 22, colW, rowH, 3, points);
  });
  if (caseGroups.length) y += Math.ceil(caseGroups.length / cols) * cellH + 12;
  for (const group of otherGroups) {
    const members = nodes.filter((node) => (node.group || "other") === group);
    const gridCols = 12;
    labels.push({ id: KIND_LABEL[group] || group, x: 16, y });
    y += 22;
    placeGrid(members, 16, y, colW, rowH, gridCols, points);
    y += Math.ceil(members.length / gridCols) * rowH + 18;
  }
  return { points, labels, w: Math.max(cols * cellW, 12 * colW) + 32, h: y + 16 };
}

function placeGrid(nodes: GraphNode[], x: number, y: number, colW: number, rowH: number, cols: number, points: Record<string, Point>) {
  nodes.forEach((node, index) => {
    points[node.id] = { x: x + (index % cols) * colW, y: y + Math.floor(index / cols) * rowH };
  });
}

function circle(nodes: GraphNode[], size: { w: number; h: number }, cx: number, cy: number, radius: number): Record<string, Point> {
  const points: Record<string, Point> = {};
  nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    points[node.id] = { x: cx - 70 + Math.cos(angle) * radius, y: cy - 28 + Math.sin(angle) * radius * 0.72 };
  });
  return points;
}
