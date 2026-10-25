"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";
import { ObsidianGraph } from "@/components/ObsidianGraph";
import { RadialWebGraph } from "@/components/RadialWebGraph";

type Point = { x: number; y: number };
type Label = { id: string; x: number; y: number };

export function EvidenceGraph({
  nodes,
  edges,
  selected,
  focused,
  viewMode = "web",
  rootId,
  onRootChange,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  focused?: string | null;
  viewMode?: "web" | "atom" | "structure";
  rootId?: string | null;
  onRootChange?: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  if (viewMode === "structure") {
    return <CardGraph nodes={nodes} edges={edges} selected={selected} focused={focused} onSelect={onSelect} />;
  }
  if (viewMode === "atom") {
    return <ObsidianGraph nodes={nodes} edges={edges} selected={selected} focused={focused} onSelect={onSelect} />;
  }
  return (
    <RadialWebGraph
      nodes={nodes}
      edges={edges}
      initialRootId={rootId || (focused ? (focused === "CASE-011" ? "cell:D3" : undefined) : undefined)}
      onRootChange={onRootChange}
      selected={selected}
      onSelect={onSelect}
    />
  );
}

function CardGraph({
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
  const [size, setSize] = useState({ w: 780, h: 580 });
  const [draggedPositions, setDraggedPositions] = useState<Record<string, Point>>({});

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => setSize({ w: Math.max(element.clientWidth, 720), h: Math.max(element.clientHeight, 540) });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const signature = `${nodes.length}:${focused || "all"}:${nodes.map((node) => node.group || "").join("|")}`;
  const arranged = useMemo(() => layoutNodes(nodes, size, focused), [signature, size.w, size.h, focused]);

  const placed = useMemo(() => {
    const res: Record<string, Point> = { ...arranged.points };
    for (const [id, pt] of Object.entries(draggedPositions)) {
      if (res[id]) res[id] = pt;
    }
    return res;
  }, [arranged.points, draggedPositions]);

  const boardWidth = Math.max(arranged.w, size.w);
  const boardHeight = Math.max(arranged.h, size.h);
  const nodeRadius = 24;

  return (
    <div className="graph-container" ref={frame} role="group" aria-label="Evidence knowledge graph">
      <div className="graph-board" style={{ width: boardWidth, height: boardHeight }}>
        {/* SVG Bezier S-Curve Connectors */}
        <svg aria-hidden="true" width={boardWidth} height={boardHeight}>
          {edges.map((edge) => {
            const from = placed[edge.source];
            const to = placed[edge.target];
            if (!from || !to) return null;

            const x1 = from.x + nodeRadius;
            const y1 = from.y + nodeRadius;
            const x2 = to.x + nodeRadius;
            const y2 = to.y + nodeRadius;

            const dx = Math.abs(x2 - x1) * 0.5;
            const c1x = x1 < x2 ? x1 + dx : x1 - dx;
            const c2x = x1 < x2 ? x2 - dx : x2 + dx;
            const pathData = `M ${x1} ${y1} C ${c1x} ${y1}, ${c2x} ${y2}, ${x2} ${y2}`;

            const isContradiction = edge.type === "CONTRADICTS" || edge.type === "AMBIGUOUS";
            const isReconciled = edge.type === "RECONCILES_WITH" || edge.type === "CORRECTS";
            const isHighlighted = selected && (edge.source === selected || edge.target === selected);

            const color = isContradiction
              ? "#FA6610"
              : isReconciled
              ? "#10B981"
              : "#A1A1AA";

            return (
              <path
                key={edge.id}
                d={pathData}
                fill="none"
                stroke={color}
                strokeWidth={isHighlighted ? "2.2" : "1.5"}
                strokeDasharray={edge.inferred || isContradiction ? "3 3" : undefined}
                opacity={selected ? (isHighlighted ? 1 : 0.25) : 0.85}
              />
            );
          })}
        </svg>

        {/* Cluster / Case Labels */}
        {arranged.labels.map((label) => (
          <div key={label.id} className="cluster-label" style={{ left: label.x, top: label.y }}>
            {label.id}
          </div>
        ))}

        {/* Circular Ring Nodes with document type inside the ring */}
        {nodes.map((node) => {
          const pt = placed[node.id] || { x: 40, y: 40 };
          const isSelected = selected === node.id;
          const isDimmed = Boolean(focused && node.group && node.group !== focused && node.group !== "policy");

          const badge = getNodeBadge(node);
          const typeClass = getNodeTypeClass(node);

          return (
            <div
              key={node.id}
              className={`ring-node-wrapper${isSelected ? " selected" : ""}${isDimmed ? " dim" : ""}`}
              style={{ left: pt.x, top: pt.y }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(node.id);
              }}
              onPointerDown={(event) => {
                event.stopPropagation();
                const startX = event.clientX;
                const startY = event.clientY;
                const originX = pt.x;
                const originY = pt.y;

                const onPointerMove = (next: PointerEvent) => {
                  const newX = Math.max(8, originX + next.clientX - startX);
                  const newY = Math.max(8, originY + next.clientY - startY);
                  setDraggedPositions((prev) => ({
                    ...prev,
                    [node.id]: { x: newX, y: newY },
                  }));
                };

                const onPointerUp = () => {
                  window.removeEventListener("pointermove", onPointerMove);
                  window.removeEventListener("pointerup", onPointerUp);
                };

                window.addEventListener("pointermove", onPointerMove);
                window.addEventListener("pointerup", onPointerUp);
              }}
            >
              <div className={`ring-node ${typeClass}`}>
                <span className="ring-node-badge">{badge}</span>
              </div>
              <div className="ring-node-label">
                <span className="ring-node-title">
                  {node.redacted ? "Withheld" : node.label}
                </span>
                <span className="ring-node-meta">
                  {node.redacted
                    ? node.available_from?.slice(5, 10) || "Future"
                    : node.amount
                    ? `$${node.amount}`
                    : ""}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function getNodeBadge(node: GraphNode): string {
  if (node.redacted) return "INV";
  const label = node.label.toUpperCase();
  if (label.includes("D3") || label === "JE-911" || label.startsWith("ACR")) return "D3";
  if (label.includes("B3")) return "B3";
  if (label.includes("C3")) return "C3";
  if (node.type === "PURCHASE_ORDER" || label.startsWith("PO")) return "PO";
  if (node.type === "EMAIL" || label.startsWith("EM") || label.startsWith("MSG")) return "EM";
  if (node.type === "INVOICE" || label.startsWith("INV")) return "INV";
  if (node.type === "CONTRACT" || label.startsWith("CTR")) return "CTR";
  if (node.type === "DECISION" || label.startsWith("JE")) return "JE";
  if (node.type === "POLICY" || label.startsWith("POL")) return "POL";
  return label.slice(0, 3);
}

function getNodeTypeClass(node: GraphNode): string {
  if (node.redacted) return "type-redacted";
  const badge = getNodeBadge(node);
  if (badge === "D3" || node.type === "DECISION") return "type-target";
  if (badge === "B3" || badge === "C3") return "type-precedent";
  if (badge === "PO") return "type-po";
  if (badge === "EM") return "type-email";
  if (badge === "INV") return "type-invoice";
  if (badge === "CTR") return "type-contract";
  if (badge === "JE") return "type-journal";
  return "type-po";
}

type Board = { points: Record<string, Point>; labels: Label[]; w: number; h: number };

function layoutNodes(nodes: GraphNode[], size: { w: number; h: number }, focused?: string | null): Board {
  const points: Record<string, Point> = {};
  const labels: Label[] = [];

  const isSingleCase = focused || nodes.length <= 16;
  if (isSingleCase) {
    const cx = size.w / 2;
    const cy = size.h / 2;

    const targetNode =
      nodes.find(
        (n) => getNodeBadge(n) === "D3" || n.type === "DECISION" || n.label.startsWith("JE") || n.label.startsWith("ACR")
      ) || nodes[0];

    const precedentNodes = nodes.filter(
      (n) => n.id !== targetNode?.id && (getNodeBadge(n) === "B3" || getNodeBadge(n) === "C3")
    );

    const otherNodes = nodes.filter(
      (n) => n.id !== targetNode?.id && !precedentNodes.some((p) => p.id === n.id)
    );

    if (targetNode) {
      points[targetNode.id] = { x: cx - 24, y: cy - 24 };
    }

    if (precedentNodes.length > 0) {
      precedentNodes.forEach((node, idx) => {
        const offset = (idx - (precedentNodes.length - 1) / 2) * 90;
        points[node.id] = { x: cx - 180, y: cy - 24 + offset };
      });
    }

    if (otherNodes.length > 0) {
      const rightX = cx + 180;
      otherNodes.forEach((node, idx) => {
        const spacing = otherNodes.length === 1 ? 0 : 80;
        const offset = (idx - (otherNodes.length - 1) / 2) * spacing;
        points[node.id] = { x: rightX, y: cy - 24 + offset };
      });
    }

    return { points, labels: [], w: size.w, h: size.h };
  }

  const caseGroups = [...new Set(nodes.map((n) => n.group || "other"))].sort();
  const cellW = 280;
  const cellH = 180;
  const cols = Math.max(2, Math.floor((size.w - 40) / cellW));

  caseGroups.forEach((group, gIdx) => {
    const members = nodes.filter((n) => (n.group || "other") === group);
    const col = gIdx % cols;
    const row = Math.floor(gIdx / cols);
    const gx = 32 + col * cellW;
    const gy = 32 + row * cellH;

    labels.push({ id: group === "other" ? "General" : group, x: gx, y: gy });

    members.forEach((node, mIdx) => {
      const mCol = mIdx % 3;
      const mRow = Math.floor(mIdx / 3);
      points[node.id] = {
        x: gx + mCol * 78,
        y: gy + 26 + mRow * 74,
      };
    });
  });

  const totalRows = Math.ceil(caseGroups.length / cols);
  const totalHeight = Math.max(size.h, totalRows * cellH + 60);
  const totalWidth = Math.max(size.w, cols * cellW + 40);

  return { points, labels, w: totalWidth, h: totalHeight };
}
