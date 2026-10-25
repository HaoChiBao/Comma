"use client";

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Point = { x: number; y: number };

type RadialNode = {
  id: string;
  type: string;
  label: string;
  amount: string | null;
  title?: string;
  depth: number;
  theta: number;
  x: number;
  y: number;
  parentId: string | null;
  isRoot: boolean;
  isOpen: boolean;
  isGroup: boolean;
  groupCount?: number;
  groupType?: string;
  groupMembers?: GraphNode[];
};

type HistorySnapshot = {
  visibleIds: string[];
  openIds: string[];
  selectedId: string | null;
  parentMap: [string, string][];
  childrenMap: [string, string[]][];
  groupMap: [string, any][];
};

const TYPE_COLORS: Record<string, string> = {
  DECISION: "#FA6610", // Comma orange
  TRANSACTION: "#FA6610",
  PURCHASE_ORDER: "#10B981", // Emerald green
  INVOICE: "#F59E0B", // Amber
  EMAIL: "#2563EB", // Tech blue
  CONTRACT: "#8B5CF6", // Purple
  POLICY_VERSION: "#8B5CF6",
  VENDOR: "#06B6D4", // Cyan
  ENTITY: "#06B6D4",
  PRECEDENT: "#4F46E5", // Indigo
  CELL: "#4F46E5",
  DEFAULT: "#64748B", // Slate
};

function getNodeColor(type: string): string {
  const norm = (type || "").toUpperCase().replace(/\s+/g, "_");
  return TYPE_COLORS[norm] || TYPE_COLORS.DEFAULT;
}

export function RadialWebGraph({
  nodes,
  edges,
  rootId,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  rootId?: string | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [viewBox, setViewBox] = useState({ x: -450, y: -360, w: 900, h: 720 });
  const [camera, setCamera] = useState({ x: 0, y: 0, k: 1 });
  const isPanning = useRef<{ startX: number; startY: number; camX: number; camY: number } | null>(null);

  // 1. Build Adjacency Map once
  const nodeMap = useMemo(() => {
    const map = new Map<string, GraphNode>();
    for (const n of nodes) map.set(n.id, n);
    return map;
  }, [nodes]);

  const { adjacency, edgeMap } = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    const emap = new Map<string, GraphEdge>();

    for (const edge of edges) {
      const u = edge.source;
      const v = edge.target;
      if (!adj.has(u)) adj.set(u, new Set());
      if (!adj.has(v)) adj.set(v, new Set());
      adj.get(u)!.add(v);
      adj.get(v)!.add(u);

      const key1 = `${u}|${v}`;
      const key2 = `${v}|${u}`;
      emap.set(key1, edge);
      emap.set(key2, edge);
    }
    return { adjacency: adj, edgeMap: emap };
  }, [edges]);

  // Determine Effective Root
  const effectiveRootId = useMemo(() => {
    if (rootId && nodeMap.has(rootId)) return rootId;
    if (selected && nodeMap.has(selected)) return selected;
    // Find decision or highest degree node
    let best = nodes[0]?.id || "";
    let bestDeg = -1;
    for (const n of nodes) {
      if (n.type === "DECISION" || n.label.startsWith("JE") || n.id.includes("cell:D3")) {
        return n.id;
      }
      const deg = adjacency.get(n.id)?.size || 0;
      if (deg > bestDeg) {
        bestDeg = deg;
        best = n.id;
      }
    }
    return best;
  }, [rootId, selected, nodes, nodeMap, adjacency]);

  // State: Radial Tree Structure
  const [treeParents, setTreeParents] = useState<Map<string, string>>(new Map());
  const [treeChildren, setTreeChildren] = useState<Map<string, string[]>>(new Map());
  const [openSet, setOpenSet] = useState<Set<string>>(new Set());
  const [visibleIds, setVisibleIds] = useState<Set<string>>(new Set());
  const [groupNodes, setGroupNodes] = useState<Map<string, any>>(new Map());
  const [crossLinks, setCrossLinks] = useState<{ from: string; to: string }[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  // Undo History Stack (Max 40)
  const historyStack = useRef<HistorySnapshot[]>([]);

  // Gliding Animation Coordinates
  const [animPositions, setAnimPositions] = useState<Map<string, Point>>(new Map());
  const prevPositions = useRef<Map<string, Point>>(new Map());

  // Hover state
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  // Helper to push history
  const pushHistory = useCallback(() => {
    const snap: HistorySnapshot = {
      visibleIds: Array.from(visibleIds),
      openIds: Array.from(openSet),
      selectedId: selected,
      parentMap: Array.from(treeParents.entries()),
      childrenMap: Array.from(treeChildren.entries()),
      groupMap: Array.from(groupNodes.entries()),
    };
    historyStack.current = [snap, ...historyStack.current].slice(0, 40);
  }, [visibleIds, openSet, selected, treeParents, treeChildren, groupNodes]);

  // Handle Undo / Back
  const handleBack = useCallback(() => {
    if (historyStack.current.length === 0) return;
    const [prev, ...rest] = historyStack.current;
    historyStack.current = rest;

    setVisibleIds(new Set(prev.visibleIds));
    setOpenSet(new Set(prev.openIds));
    setTreeParents(new Map(prev.parentMap));
    setTreeChildren(new Map(prev.childrenMap));
    setGroupNodes(new Map(prev.groupMap));
    setCrossLinks([]);
    if (prev.selectedId) onSelect(prev.selectedId);
  }, [onSelect]);

  // Initialize Radial Tree (Root + Immediate Neighbors)
  useEffect(() => {
    if (!effectiveRootId) return;

    const root = effectiveRootId;
    const neighbors = Array.from(adjacency.get(root) || []);

    const newVisible = new Set<string>([root]);
    const newParents = new Map<string, string>();
    const newChildren = new Map<string, string[]>();
    const newGroups = new Map<string, any>();
    const newOpen = new Set<string>([root]);

    // Group neighbors by type if > 3
    const byType = new Map<string, GraphNode[]>();
    for (const nid of neighbors) {
      const node = nodeMap.get(nid);
      if (!node) continue;
      const t = node.type || "OTHER";
      const list = byType.get(t) || [];
      list.push(node);
      byType.set(t, list);
    }

    const rootKids: string[] = [];
    byType.forEach((items, type) => {
      if (items.length > 3) {
        // Create Group Node
        const gid = `group:${root}:${type}:0`;
        newGroups.set(gid, {
          id: gid,
          type,
          label: `${items.length} ${pluralize(type)}`,
          count: items.length,
          members: items,
          parentId: root,
        });
        newVisible.add(gid);
        newParents.set(gid, root);
        rootKids.push(gid);
      } else {
        for (const item of items) {
          newVisible.add(item.id);
          newParents.set(item.id, root);
          rootKids.push(item.id);
        }
      }
    });

    newChildren.set(root, rootKids);

    setVisibleIds(newVisible);
    setTreeParents(newParents);
    setTreeChildren(newChildren);
    setGroupNodes(newGroups);
    setOpenSet(newOpen);
    setCrossLinks([]);
    historyStack.current = [];
  }, [effectiveRootId, adjacency, nodeMap]);

  // Compute Tree Angles & Radii
  const treeNodes = useMemo(() => {
    if (!effectiveRootId || visibleIds.size === 0) return [];

    // 1. Calculate subtree leaf weight for each node
    const weights = new Map<string, number>();
    function calcWeight(id: string): number {
      const kids = (treeChildren.get(id) || []).filter((k) => visibleIds.has(k));
      if (kids.length === 0) {
        weights.set(id, 1);
        return 1;
      }
      let sum = 0;
      for (const k of kids) sum += calcWeight(k);
      weights.set(id, sum);
      return sum;
    }
    calcWeight(effectiveRootId);

    // 2. Find Max Depth
    let maxD = 1;
    function findMaxDepth(id: string, depth: number) {
      if (depth > maxD) maxD = depth;
      const kids = (treeChildren.get(id) || []).filter((k) => visibleIds.has(k));
      for (const k of kids) findMaxDepth(k, depth + 1);
    }
    findMaxDepth(effectiveRootId, 0);

    const radiusPerDepth = Math.min(160, 345 / Math.max(1, maxD));

    // 3. Radial positioning
    const result: RadialNode[] = [];

    function layoutSubtree(id: string, depth: number, startTheta: number, endTheta: number) {
      const isRoot = id === effectiveRootId;
      const theta = isRoot ? 0 : (startTheta + endTheta) / 2;
      const R = isRoot ? 0 : depth * radiusPerDepth;
      const x = R * Math.cos(theta);
      const y = R * Math.sin(theta) * 0.92; // Squash y by 0.92 per spec

      let nodeData = nodeMap.get(id);
      const isGroup = id.startsWith("group:");
      const groupData = groupNodes.get(id);

      result.push({
        id,
        type: isGroup ? groupData?.type || "GROUP" : nodeData?.type || "DOCUMENT",
        label: isGroup ? groupData?.label || "Group" : nodeData?.label || id,
        amount: isGroup ? null : nodeData?.amount || null,
        title: isGroup ? `Group of ${groupData?.count} ${groupData?.type}` : nodeData?.title || nodeData?.label,
        depth,
        theta,
        x,
        y,
        parentId: treeParents.get(id) || null,
        isRoot,
        isOpen: openSet.has(id),
        isGroup,
        groupCount: groupData?.count,
        groupType: groupData?.type,
        groupMembers: groupData?.members,
      });

      const kids = (treeChildren.get(id) || []).filter((k) => visibleIds.has(k));
      if (kids.length === 0) return;

      const totalW = weights.get(id) || 1;
      const totalSpan = endTheta - startTheta;

      let currentTheta = startTheta;
      for (const k of kids) {
        const kw = weights.get(k) || 1;
        const kidSpan = (kw / totalW) * totalSpan;
        layoutSubtree(k, depth + 1, currentTheta, currentTheta + kidSpan);
        currentTheta += kidSpan;
      }
    }

    layoutSubtree(effectiveRootId, 0, -Math.PI, Math.PI);
    return result;
  }, [effectiveRootId, visibleIds, treeChildren, treeParents, openSet, nodeMap, groupNodes]);

  // Max depth for concentric rings
  const maxDepth = useMemo(() => {
    let m = 1;
    for (const n of treeNodes) {
      if (n.depth > m) m = n.depth;
    }
    return m;
  }, [treeNodes]);

  const radiusPerDepth = Math.min(160, 345 / Math.max(1, maxDepth));

  // Gliding Animation: Glide from parent on next frame
  useEffect(() => {
    const nextMap = new Map<string, Point>();
    const pMap = prevPositions.current;

    for (const node of treeNodes) {
      nextMap.set(node.id, { x: node.x, y: node.y });
    }

    // New nodes start at their parent's coordinate
    const animMap = new Map<string, Point>();
    for (const node of treeNodes) {
      if (!pMap.has(node.id) && node.parentId && pMap.has(node.parentId)) {
        animMap.set(node.id, pMap.get(node.parentId)!);
      } else if (pMap.has(node.id)) {
        animMap.set(node.id, pMap.get(node.id)!);
      } else {
        animMap.set(node.id, { x: node.x, y: node.y });
      }
    }
    setAnimPositions(animMap);

    // Double requestAnimationFrame to trigger CSS transition outward
    let f1 = requestAnimationFrame(() => {
      let f2 = requestAnimationFrame(() => {
        setAnimPositions(nextMap);
        prevPositions.current = nextMap;
      });
      return () => cancelAnimationFrame(f2);
    });

    return () => cancelAnimationFrame(f1);
  }, [treeNodes]);

  // Click Handler (Expand, Collapse, Group, Cross-Links)
  const handleNodeClick = useCallback(
    (node: RadialNode) => {
      onSelect(node.id);

      // 1. Group Node Click: Expand up to 8 members
      if (node.isGroup && node.groupMembers) {
        pushHistory();
        const pId = node.parentId || effectiveRootId;
        const allMembers = node.groupMembers;
        const reveal = allMembers.slice(0, 8);
        const remainder = allMembers.slice(8);

        const newVis = new Set(visibleIds);
        newVis.delete(node.id);

        const newParents = new Map(treeParents);
        newParents.delete(node.id);

        const newGroups = new Map(groupNodes);
        newGroups.delete(node.id);

        const newKids = (treeChildren.get(pId) || []).filter((k) => k !== node.id);

        for (const item of reveal) {
          newVis.add(item.id);
          newParents.set(item.id, pId);
          newKids.push(item.id);
        }

        if (remainder.length > 0) {
          const nextGid = `group:${pId}:${node.groupType || "ITEM"}:${Date.now()}`;
          newGroups.set(nextGid, {
            id: nextGid,
            type: node.groupType,
            label: `+${remainder.length} ${pluralize(node.groupType || "")}`,
            count: remainder.length,
            members: remainder,
            parentId: pId,
          });
          newVis.add(nextGid);
          newParents.set(nextGid, pId);
          newKids.push(nextGid);
        }

        const newChildrenMap = new Map(treeChildren);
        newChildrenMap.set(pId, newKids);

        setVisibleIds(newVis);
        setTreeParents(newParents);
        setTreeChildren(newChildrenMap);
        setGroupNodes(newGroups);
        return;
      }

      // 2. Already Open Node: Collapse descendants
      if (openSet.has(node.id) && !node.isRoot) {
        pushHistory();
        const toRemove = new Set<string>();
        function collectDescendants(id: string) {
          const kids = treeChildren.get(id) || [];
          for (const k of kids) {
            toRemove.add(k);
            collectDescendants(k);
          }
        }
        collectDescendants(node.id);

        const newVis = new Set(visibleIds);
        const newOpen = new Set(openSet);
        newOpen.delete(node.id);
        for (const id of toRemove) {
          newVis.delete(id);
          newOpen.delete(id);
        }

        const newKidsMap = new Map(treeChildren);
        newKidsMap.delete(node.id);

        setVisibleIds(newVis);
        setOpenSet(newOpen);
        setTreeChildren(newKidsMap);
        setCrossLinks([]);
        return;
      }

      // 3. Unopened Node: Check neighbors
      const allNeighbors = Array.from(adjacency.get(node.id) || []);
      const newNeighbors = allNeighbors.filter((nid) => !visibleIds.has(nid));

      // 4. If all neighbors already on screen: draw cross-links and show toast
      if (newNeighbors.length === 0) {
        if (allNeighbors.length > 0) {
          setCrossLinks(allNeighbors.map((target) => ({ from: node.id, to: target })));
          setToast(`Everything linked to ${node.label} is already on screen.`);
          setTimeout(() => setToast(null), 3000);
        } else {
          setToast(`No linked documents for ${node.label}.`);
          setTimeout(() => setToast(null), 2500);
        }
        return;
      }

      // 5. Expand New Neighbors
      pushHistory();
      const byType = new Map<string, GraphNode[]>();
      for (const nid of newNeighbors) {
        const item = nodeMap.get(nid);
        if (!item) continue;
        const t = item.type || "OTHER";
        const list = byType.get(t) || [];
        list.push(item);
        byType.set(t, list);
      }

      const newVis = new Set(visibleIds);
      const newParents = new Map(treeParents);
      const newGroups = new Map(groupNodes);
      const newOpen = new Set(openSet);
      newOpen.add(node.id);

      const addedKids: string[] = [];
      byType.forEach((items, type) => {
        if (items.length > 3) {
          const gid = `group:${node.id}:${type}:${Date.now()}`;
          newGroups.set(gid, {
            id: gid,
            type,
            label: `${items.length} ${pluralize(type)}`,
            count: items.length,
            members: items,
            parentId: node.id,
          });
          newVis.add(gid);
          newParents.set(gid, node.id);
          addedKids.push(gid);
        } else {
          for (const item of items) {
            newVis.add(item.id);
            newParents.set(item.id, node.id);
            addedKids.push(item.id);
          }
        }
      });

      const newKidsMap = new Map(treeChildren);
      newKidsMap.set(node.id, addedKids);

      setVisibleIds(newVis);
      setOpenSet(newOpen);
      setTreeParents(newParents);
      setTreeChildren(newKidsMap);
      setGroupNodes(newGroups);
      setCrossLinks([]);
    },
    [
      onSelect,
      openSet,
      visibleIds,
      treeChildren,
      treeParents,
      groupNodes,
      adjacency,
      nodeMap,
      effectiveRootId,
      pushHistory,
    ]
  );

  // Resize Observer
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const updateSize = () => {
      const w = el.clientWidth || 900;
      const h = el.clientHeight || 720;
      setViewBox({ x: -w / 2, y: -h / 2, w, h });
    };
    updateSize();
    const obs = new ResizeObserver(updateSize);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Trackpad Pinch-to-Zoom
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.015 : 0.002));
      setCamera((c) => ({
        ...c,
        k: Math.min(3.0, Math.max(0.3, c.k * factor)),
      }));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const posMap = useMemo(() => {
    const map = new Map<string, Point>();
    for (const n of treeNodes) {
      map.set(n.id, animPositions.get(n.id) || { x: n.x, y: n.y });
    }
    return map;
  }, [treeNodes, animPositions]);

  return (
    <div
      className="radial-web-container"
      ref={containerRef}
      onPointerDown={(e) => {
        if (e.button === 0 && (e.target as HTMLElement).tagName === "svg") {
          isPanning.current = {
            startX: e.clientX,
            startY: e.clientY,
            camX: camera.x,
            camY: camera.y,
          };
          e.currentTarget.setPointerCapture(e.pointerId);
        }
      }}
      onPointerMove={(e) => {
        if (isPanning.current) {
          const dx = e.clientX - isPanning.current.startX;
          const dy = e.clientY - isPanning.current.startY;
          setCamera((c) => ({ ...c, x: isPanning.current!.camX + dx, y: isPanning.current!.camY + dy }));
        }
      }}
      onPointerUp={() => {
        isPanning.current = null;
      }}
    >
      {/* Floating Toolbar: Back Button & Depth Indicator */}
      <div className="radial-web-toolbar">
        <button
          className="web-btn"
          onClick={handleBack}
          disabled={historyStack.current.length === 0}
          title="Undo last node expansion (Back)"
        >
          ↶ Back
        </button>
        <span className="web-pill">
          Depth {maxDepth} · {visibleIds.size} visible
        </span>
      </div>

      {/* Cross-Link Toast Notification */}
      {toast && (
        <div className="web-toast" role="status">
          {toast}
        </div>
      )}

      {/* SVG Canvas */}
      <svg
        className="radial-svg"
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
      >
        <g transform={`translate(${camera.x}, ${camera.y}) scale(${camera.k})`}>
          {/* 1. Faint Concentric Guide Rings */}
          {Array.from({ length: maxDepth }).map((_, idx) => {
            const d = idx + 1;
            const rx = d * radiusPerDepth;
            const ry = rx * 0.92;
            return (
              <ellipse
                key={`ring-${d}`}
                cx={0}
                cy={0}
                rx={rx}
                ry={ry}
                fill="none"
                stroke="rgba(13, 13, 12, 0.06)"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            );
          })}

          {/* 2. Radial Tree Edges */}
          {treeNodes
            .filter((n) => !n.isRoot && n.parentId)
            .map((n) => {
              const p = posMap.get(n.parentId!);
              const c = posMap.get(n.id);
              if (!p || !c) return null;

              const edgeData = edgeMap.get(`${n.parentId}|${n.id}`) || edgeMap.get(`${n.id}|${n.parentId}`);
              const edgeType = edgeData?.type || "CONNECTS";
              const isSelected = selected === n.id || selected === n.parentId;
              const isContradiction = edgeType === "CONTRADICTS";
              const isSupports = edgeType === "RECONCILES_WITH" || edgeType === "POTENTIALLY_SUPPORTS";
              const isDashed = n.isGroup || edgeType === "POTENTIALLY_SUPPORTS";

              const strokeColor = isContradiction
                ? "#FA6610"
                : isSupports
                ? "#10B981"
                : isSelected
                ? "rgba(13, 13, 12, 0.65)"
                : "rgba(13, 13, 12, 0.18)";

              const strokeW = isSelected ? 1.8 : 1.1;

              return (
                <line
                  key={`edge-${n.parentId}-${n.id}`}
                  x1={p.x}
                  y1={p.y}
                  x2={c.x}
                  y2={c.y}
                  stroke={strokeColor}
                  strokeWidth={strokeW}
                  strokeDasharray={isDashed ? "4 4" : "none"}
                  style={{
                    transition: "x1 .6s cubic-bezier(.2,.8,.2,1), y1 .6s cubic-bezier(.2,.8,.2,1), x2 .6s cubic-bezier(.2,.8,.2,1), y2 .6s cubic-bezier(.2,.8,.2,1)",
                  }}
                />
              );
            })}

          {/* 3. Cross-Link Curved Dashed Lines */}
          {crossLinks.map((cl, i) => {
            const p1 = posMap.get(cl.from);
            const p2 = posMap.get(cl.to);
            if (!p1 || !p2) return null;

            // Curved quadratic bezier arc
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const normalX = -dy * 0.25;
            const normalY = dx * 0.25;
            const cpx = midX + normalX;
            const cpy = midY + normalY;

            return (
              <path
                key={`cross-${cl.from}-${cl.to}-${i}`}
                d={`M ${p1.x} ${p1.y} Q ${cpx} ${cpy} ${p2.x} ${p2.y}`}
                fill="none"
                stroke="#FA6610"
                strokeWidth={1.4}
                strokeDasharray="4 4"
                opacity={0.85}
              />
            );
          })}

          {/* 4. Nodes */}
          {treeNodes.map((n) => {
            const pos = posMap.get(n.id) || { x: n.x, y: n.y };
            const isSelected = selected === n.id;
            const isHovered = hoveredId === n.id;
            const color = getNodeColor(n.type);
            const r = n.isRoot ? 12 : 7.5;

            // Fill & Stroke logic per spec:
            // Fill = type color when root or opened; white with type-colored stroke otherwise.
            const isFilled = n.isRoot || n.isOpen;
            const fill = isFilled ? color : "#FFFFFF";
            const stroke = isSelected || isHovered ? "#0D0D0C" : color;
            const strokeWidth = isSelected ? 2.5 : isHovered ? 2.0 : isFilled ? 1.5 : 2.0;

            // Label positioning facing away from center
            const isRight = Math.cos(n.theta) >= 0;
            const labelX = n.isRoot ? 0 : isRight ? r + 8 : -(r + 8);
            const labelY = n.isRoot ? r + 15 : 3.5;
            const anchor = n.isRoot ? "middle" : isRight ? "start" : "end";

            return (
              <g
                key={n.id}
                className="radial-node-group"
                style={{
                  transform: `translate(${pos.x}px, ${pos.y}px)`,
                  transition: "transform 0.6s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.4s ease",
                  cursor: "pointer",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  handleNodeClick(n);
                }}
                onPointerEnter={() => setHoveredId(n.id)}
                onPointerLeave={() => setHoveredId(null)}
              >
                {/* Selected Halo */}
                {isSelected && (
                  <circle
                    r={r + 5.5}
                    fill="none"
                    stroke="#0D0D0C"
                    strokeWidth={1.5}
                    opacity={0.3}
                  />
                )}

                {/* Group Node: 3 Offset Hollow Circles */}
                {n.isGroup ? (
                  <g className="group-circles">
                    <circle cx={-3} cy={-2} r={r} fill="#FFFFFF" stroke={color} strokeWidth={1.5} />
                    <circle cx={0} cy={0} r={r} fill="#FFFFFF" stroke={color} strokeWidth={1.5} />
                    <circle cx={3} cy={2} r={r} fill="#FFFFFF" stroke="#0D0D0C" strokeWidth={1.5} />
                  </g>
                ) : (
                  /* Standard Node Circle */
                  <circle
                    r={r}
                    fill={fill}
                    stroke={stroke}
                    strokeWidth={strokeWidth}
                  />
                )}

                {/* Inner Dot for Opened Nodes if hollow */}
                {!isFilled && !n.isGroup && (
                  <circle r={2.5} fill={color} />
                )}

                {/* Label (Always shown for root or selected; clean display for others) */}
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor={anchor}
                  className="radial-node-label"
                  fill={isSelected ? "#0D0D0C" : "rgba(13, 13, 12, 0.85)"}
                  fontWeight={isSelected ? 700 : n.isRoot ? 600 : 500}
                  fontSize={n.isRoot ? 12 : 11}
                  fontFamily="'Saans', -apple-system, sans-serif"
                >
                  {n.isGroup
                    ? n.label
                    : `${n.label}${n.amount ? ` · $${n.amount}` : ""}`}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

function pluralize(type: string): string {
  const norm = type.toLowerCase();
  if (norm.includes("invoice")) return "invoices";
  if (norm.includes("email")) return "emails";
  if (norm.includes("purchase") || norm.includes("order")) return "purchase orders";
  if (norm.includes("transaction")) return "transactions";
  if (norm.includes("decision")) return "decisions";
  if (norm.includes("vendor")) return "vendors";
  return `${norm}s`;
}
