"use client";

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Point = { x: number; y: number };

type WebNode = {
  id: string;
  type: string;
  label: string;
  amount: string | null;
  title?: string;
  depth: number;
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
  initialRootId,
  onRootChange,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  initialRootId?: string | null;
  onRootChange?: (newRoot: string) => void;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ w: 1000, h: 700 });
  const [camera, setCamera] = useState({ x: -280, y: 0, k: 1 });
  const isPanning = useRef<{ startX: number; startY: number; camX: number; camY: number } | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      setDimensions({ w: el.clientWidth || 1000, h: el.clientHeight || 700 });
    };
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

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

      emap.set(`${u}|${v}`, edge);
      emap.set(`${v}|${u}`, edge);
    }
    return { adjacency: adj, edgeMap: emap };
  }, [edges]);

  // Root ID state
  const [rootId, setRootId] = useState<string>("");
  const [spotlightMode, setSpotlightMode] = useState<boolean>(true);

  // Root Candidate list for the root picker dropdown
  const rootCandidates = useMemo(() => {
    const list = nodes.filter((n) => n.type === "DECISION");
    if (list.length === 0) return nodes.slice(0, 8);
    return list;
  }, [nodes]);

  useEffect(() => {
    if (nodes.length === 0) return;

    if (initialRootId && nodeMap.has(initialRootId)) {
      setRootId(initialRootId);
      return;
    }
    if (rootId && nodeMap.has(rootId)) return;

    const decision = nodes.find((n) => n.type === "DECISION" || n.label.startsWith("JE"));
    if (decision) {
      setRootId(decision.id);
      return;
    }

    let best = nodes[0]?.id || "";
    let bestDeg = -1;
    for (const n of nodes) {
      const deg = adjacency.get(n.id)?.size || 0;
      if (deg > bestDeg) {
        bestDeg = deg;
        best = n.id;
      }
    }
    if (best) setRootId(best);
  }, [initialRootId, nodes, nodeMap, adjacency, rootId]);

  // Tree State
  const [treeParents, setTreeParents] = useState<Map<string, string>>(new Map());
  const [treeChildren, setTreeChildren] = useState<Map<string, string[]>>(new Map());
  const [openSet, setOpenSet] = useState<Set<string>>(new Set());
  const [visibleIds, setVisibleIds] = useState<Set<string>>(new Set());
  const [groupNodes, setGroupNodes] = useState<Map<string, any>>(new Map());
  const [crossLinks, setCrossLinks] = useState<{ from: string; to: string }[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  // Undo History Stack (Max 40)
  const historyStack = useRef<HistorySnapshot[]>([]);

  // Positions for transitions
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  // Push history snapshot
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

  // Reset to initial root view
  const handleResetToRoot = useCallback(() => {
    if (!rootId) return;
    pushHistory();
    const neighbors = Array.from(adjacency.get(rootId) || []);

    const newVisible = new Set<string>([rootId]);
    const newParents = new Map<string, string>();
    const newChildren = new Map<string, string[]>();
    const newGroups = new Map<string, any>();
    const newOpen = new Set<string>([rootId]);

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
        const gid = `group:${rootId}:${type}:0`;
        newGroups.set(gid, {
          id: gid,
          type,
          label: `${items.length} ${pluralize(type)}`,
          count: items.length,
          members: items,
          parentId: rootId,
        });
        newVisible.add(gid);
        newParents.set(gid, rootId);
        rootKids.push(gid);
      } else {
        for (const item of items) {
          newVisible.add(item.id);
          newParents.set(item.id, rootId);
          rootKids.push(item.id);
        }
      }
    });

    newChildren.set(rootId, rootKids);
    setVisibleIds(newVisible);
    setTreeParents(newParents);
    setTreeChildren(newChildren);
    setGroupNodes(newGroups);
    setOpenSet(newOpen);
    setCrossLinks([]);
    onSelect(rootId);
  }, [rootId, adjacency, nodeMap, onSelect, pushHistory]);

  // Initial Population when rootId is set
  useEffect(() => {
    if (!rootId || !nodeMap.has(rootId)) return;

    const neighbors = Array.from(adjacency.get(rootId) || []);
    const newVisible = new Set<string>([rootId]);
    const newParents = new Map<string, string>();
    const newChildren = new Map<string, string[]>();
    const newGroups = new Map<string, any>();
    const newOpen = new Set<string>([rootId]);

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
        const gid = `group:${rootId}:${type}:0`;
        newGroups.set(gid, {
          id: gid,
          type,
          label: `${items.length} ${pluralize(type)}`,
          count: items.length,
          members: items,
          parentId: rootId,
        });
        newVisible.add(gid);
        newParents.set(gid, rootId);
        rootKids.push(gid);
      } else {
        for (const item of items) {
          newVisible.add(item.id);
          newParents.set(item.id, rootId);
          rootKids.push(item.id);
        }
      }
    });

    newChildren.set(rootId, rootKids);
    setVisibleIds(newVisible);
    setTreeParents(newParents);
    setTreeChildren(newChildren);
    setGroupNodes(newGroups);
    setOpenSet(newOpen);
    setCrossLinks([]);
    historyStack.current = [];
  }, [rootId, adjacency, nodeMap]);

  // Tidy Horizontal Tree Layout (Constant Flow Left-to-Right)
  const treeNodes = useMemo(() => {
    if (!rootId || visibleIds.size === 0) return [];

    const COLUMN_GAP = 230;
    const ROW_GAP = 50;

    // 1. Assign depths
    const depths = new Map<string, number>();
    depths.set(rootId, 0);

    function calcDepths(id: string, d: number) {
      depths.set(id, d);
      const kids = (treeChildren.get(id) || []).filter((k) => visibleIds.has(k));
      for (const k of kids) calcDepths(k, d + 1);
    }
    calcDepths(rootId, 0);

    // 2. Tidy Vertical Layout: compute leaf slots in post-order
    let currentY = 0;
    const yMap = new Map<string, number>();

    function assignY(id: string) {
      const kids = (treeChildren.get(id) || []).filter((k) => visibleIds.has(k));
      if (kids.length === 0) {
        yMap.set(id, currentY);
        currentY += ROW_GAP;
        return;
      }
      for (const k of kids) assignY(k);
      // Parent Y is vertically centered between first and last child
      const firstY = yMap.get(kids[0])!;
      const lastY = yMap.get(kids[kids.length - 1])!;
      yMap.set(id, (firstY + lastY) / 2);
    }

    assignY(rootId);

    // 3. Center whole tree vertically
    const rootY = yMap.get(rootId) || 0;

    const result: WebNode[] = [];
    for (const id of Array.from(visibleIds)) {
      const isRoot = id === rootId;
      const depth = depths.get(id) ?? 0;
      const x = depth * COLUMN_GAP;
      const y = (yMap.get(id) ?? 0) - rootY;

      const isGroup = id.startsWith("group:");
      const groupData = groupNodes.get(id);
      const nodeData = nodeMap.get(id);

      result.push({
        id,
        type: isGroup ? groupData?.type || "GROUP" : nodeData?.type || "DOCUMENT",
        label: isGroup ? groupData?.label || "Group" : nodeData?.label || id,
        amount: isGroup ? null : nodeData?.amount || null,
        title: isGroup ? `Group of ${groupData?.count} ${groupData?.type}` : nodeData?.title || nodeData?.label,
        depth,
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
    }

    return result;
  }, [rootId, visibleIds, treeChildren, treeParents, openSet, nodeMap, groupNodes]);

  // Click Handler (Expand, Collapse, Group, Cross-Links)
  const handleNodeClick = useCallback(
    (node: WebNode) => {
      // 1. Always select the node for the inspector
      onSelect(node.id);

      // 2. Group Node Click: Expand up to 8 members
      if (node.isGroup && node.groupMembers) {
        pushHistory();
        const pId = node.parentId || rootId;
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

      // 3. Already Open Node: Collapse descendants (root stays open)
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
        for (const id of Array.from(toRemove)) {
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

      // 4. Unopened Node: Check neighbors
      const allNeighbors = Array.from(adjacency.get(node.id) || []);
      const newNeighbors = allNeighbors.filter((nid) => !visibleIds.has(nid));

      // 5. If all neighbors already on screen: draw cross-links and show toast
      if (newNeighbors.length === 0) {
        if (allNeighbors.length > 0) {
          setCrossLinks(allNeighbors.map((target) => ({ from: node.id, to: target })));
          setToast(`Everything linked to ${node.label} is already on screen.`);
          setTimeout(() => setToast(null), 2800);
        } else {
          setToast(`No linked documents for ${node.label}.`);
          setTimeout(() => setToast(null), 2400);
        }
        return;
      }

      // 6. Expand New Neighbors (Flow to the Right)
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
      rootId,
      pushHistory,
    ]
  );

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
      map.set(n.id, { x: n.x, y: n.y });
    }
    return map;
  }, [treeNodes]);

  // Spotlight active trail: from root down to selected node, plus immediate children
  const activeTrail = useMemo(() => {
    if (!selected) return new Set<string>();
    const trail = new Set<string>();
    let cur: string | null = selected;
    while (cur) {
      trail.add(cur);
      cur = treeParents.get(cur) || null;
    }
    const kids = treeChildren.get(selected) || [];
    for (const k of kids) trail.add(k);
    return trail;
  }, [selected, treeParents, treeChildren]);

  // Dynamic Camera Auto-Framing: smoothly glides the stage to center the selected node and its immediate children
  useEffect(() => {
    if (!selected || isPanning.current) return;
    const target = treeNodes.find((n) => n.id === selected);
    if (!target) return;

    const kids = (treeChildren.get(selected) || [])
      .map((kidId) => treeNodes.find((n) => n.id === kidId))
      .filter(Boolean) as WebNode[];

    // Target focus point
    let focusX = target.x;
    let focusY = target.y;

    if (kids.length > 0) {
      focusX = target.x + 115; // Shift to frame both the selected node and its children
      const avgKidY = kids.reduce((sum, k) => sum + k.y, 0) / kids.length;
      focusY = (target.y + avgKidY) / 2;
    }

    // Visible stage area to the left of the drawer (drawer is 380px wide)
    const visibleWidth = Math.max(340, dimensions.w - 420);
    const targetScreenX = visibleWidth / 2;

    setCamera((c) => ({
      ...c,
      x: targetScreenX - dimensions.w / 2 - focusX * c.k,
      y: -focusY * c.k,
    }));
  }, [selected, treeNodes, treeChildren, dimensions.w]);

  const maxDepth = useMemo(() => {
    let m = 0;
    for (const n of treeNodes) {
      if (n.depth > m) m = n.depth;
    }
    return m;
  }, [treeNodes]);

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
        const pan = isPanning.current;
        if (pan) {
          const dx = e.clientX - pan.startX;
          const dy = e.clientY - pan.startY;
          const targetX = pan.camX + dx;
          const targetY = pan.camY + dy;
          setCamera((c) => ({ ...c, x: targetX, y: targetY }));
        }
      }}
      onPointerUp={() => {
        isPanning.current = null;
      }}
      onPointerCancel={() => {
        isPanning.current = null;
      }}
      onWheel={(e) => {
        // Smooth wheel zoom with clamp
        const factor = e.deltaY < 0 ? 1.05 : 0.95;
        setCamera((c) => ({
          ...c,
          k: Math.max(0.4, Math.min(2.0, Number((c.k * factor).toFixed(3)))),
        }));
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

        {/* Root Candidate Selector */}
        <div className="web-root-picker">
          <span className="picker-label">Root:</span>
          <select
            className="web-select"
            value={rootId}
            onChange={(e) => {
              const newRoot = e.target.value;
              setRootId(newRoot);
              onSelect(newRoot);
              onRootChange?.(newRoot);
            }}
          >
            {rootCandidates.map((cand) => (
              <option key={cand.id} value={cand.id}>
                {cand.label} {cand.amount ? `($${cand.amount})` : ""} · {cand.type}
              </option>
            ))}
          </select>
        </div>

        {/* Spotlight Focus Mode Toggle */}
        <button
          className={`web-btn ${spotlightMode ? "active" : ""}`}
          onClick={() => setSpotlightMode((s) => !s)}
          title="Toggle spotlight focus (dim unrelated branches)"
        >
          {spotlightMode ? "⊙ Focused" : "○ All Visible"}
        </button>

        {/* Zoom Controls */}
        <div className="web-zoom-group" style={{ display: "inline-flex", gap: "2px" }}>
          <button
            className="web-btn"
            style={{ padding: "0.25rem 0.55rem", fontWeight: 700 }}
            onClick={() => setCamera((c) => ({ ...c, k: Math.min(2.0, Number((c.k * 1.15).toFixed(2))) }))}
            title="Zoom In (+)"
          >
            +
          </button>
          <button
            className="web-btn"
            style={{ padding: "0.25rem 0.55rem", fontWeight: 700 }}
            onClick={() => setCamera((c) => ({ ...c, k: Math.max(0.4, Number((c.k * 0.85).toFixed(2))) }))}
            title="Zoom Out (−)"
          >
            −
          </button>
        </div>

        <span className="web-pill">
          {maxDepth + 1} Tiers · {visibleIds.size} visible
        </span>
      </div>

      {/* Cross-Link Toast Notification */}
      {toast && (
        <div className="web-toast" role="status">
          {toast}
        </div>
      )}

      {/* SVG Canvas */}
      <svg className="radial-svg">
        <g
          style={{
            transform: `translate(${dimensions.w / 2 + camera.x}px, ${dimensions.h / 2 + camera.y}px) scale(${camera.k})`,
            transition: isPanning.current ? "none" : "transform 0.32s cubic-bezier(0.23, 1, 0.32, 1)",
          }}
        >
          {/* 1. Subtle Column Tier Dividers */}
          {Array.from({ length: maxDepth + 1 }).map((_, idx) => {
            const x = idx * 230;
            return (
              <line
                key={`tier-${idx}`}
                x1={x}
                y1={-600}
                x2={x}
                y2={600}
                stroke="rgba(13, 13, 12, 0.03)"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
            );
          })}

          {/* 2. Horizontal Bezier Connectors (Constant Flow Left-to-Right) */}
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
                : "rgba(13, 13, 12, 0.16)";

              const strokeW = isSelected ? 1.8 : 1.1;

              // Check if edge is in active spotlight trail
              const isEdgeInFocus = !selected || !spotlightMode || (activeTrail.has(n.parentId!) && activeTrail.has(n.id));

              // Smooth horizontal cubic bezier curve
              const dx = c.x - p.x;
              const cpx1 = p.x + dx * 0.45;
              const cpx2 = c.x - dx * 0.45;
              const pathD = `M ${p.x} ${p.y} C ${cpx1} ${p.y}, ${cpx2} ${c.y}, ${c.x} ${c.y}`;

              return (
                <path
                  key={`edge-${n.parentId}-${n.id}`}
                  d={pathD}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth={strokeW}
                  strokeDasharray={isDashed ? "4 4" : "none"}
                  opacity={isEdgeInFocus ? 1 : 0.08}
                  style={{
                    transition: "d 0.24s cubic-bezier(0.23, 1, 0.32, 1), stroke 0.15s ease, opacity 0.22s ease",
                  }}
                />
              );
            })}

          {/* 3. Cross-Link Curved Dashed Lines */}
          {crossLinks.map((cl, i) => {
            const p1 = posMap.get(cl.from);
            const p2 = posMap.get(cl.to);
            if (!p1 || !p2) return null;

            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            const dy = p2.y - p1.y;
            const cpx = midX - Math.abs(dy) * 0.3 - 20;
            const cpy = midY;

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
            const r = n.isRoot ? 11 : 7.5;

            // Spotlight focus check: active path or children
            const isNodeInFocus = !selected || !spotlightMode || activeTrail.has(n.id);

            // Fill & Stroke logic:
            // Fill = type color when root or opened; white with type-colored stroke otherwise.
            const isFilled = n.isRoot || n.isOpen;
            const fill = isFilled ? color : "#FFFFFF";
            const stroke = isSelected || isHovered ? "#0D0D0C" : color;
            const strokeWidth = isSelected ? 2.5 : isHovered ? 2.0 : isFilled ? 1.5 : 2.0;

            // Label positioning strictly to the right of node
            const labelX = r + 8;
            const labelY = 3.5;

            return (
              <g
                key={n.id}
                className="radial-node-group"
                style={{
                  transform: `translate(${pos.x}px, ${pos.y}px)`,
                  opacity: isNodeInFocus ? 1 : 0.16,
                  transition: "transform 0.24s cubic-bezier(0.23, 1, 0.32, 1), opacity 0.22s ease",
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
                    r={r + 5}
                    fill="none"
                    stroke="#0D0D0C"
                    strokeWidth={1.5}
                    opacity={0.25}
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

                {/* Label Placed to the Right */}
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor="start"
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
