"use client";

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";
import type { AgentSuggestion, InferredLink } from "@/lib/investigate";

type Point = { x: number; y: number };

type WebNode = {
  id: string;
  type: string;
  label: string;
  documentId?: string;
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
  rootId?: string;
  upstreamTrail?: string[];
};

function clipLabel(text: string, max = 36) {
  const value = (text || "").trim();
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(1, max - 1)).trimEnd()}…`;
}

function recordCode(node: { label?: string; document_id?: string }) {
  return node.document_id || node.label || "";
}

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

function getDistinctNeighbors(
  nodeId: string,
  adjacency: Map<string, Set<string>>,
  nodeMap: Map<string, GraphNode>,
  visibleIds?: Set<string>,
  excludeLabels?: Set<string>
): GraphNode[] {
  const currentLabel = nodeMap.get(nodeId)?.label || nodeId;
  const rawNeighbors = Array.from(adjacency.get(nodeId) || []).filter((id) => id !== nodeId);
  const byLabel = new Map<string, GraphNode>();

  for (const nid of rawNeighbors) {
    if (visibleIds && visibleIds.has(nid)) continue;
    const item = nodeMap.get(nid);
    if (!item) continue;
    // Disallow linking to self or mirror records with same label
    if (item.label === currentLabel) continue;
    if (excludeLabels && excludeLabels.has(item.label)) continue;

    const existing = byLabel.get(item.label);
    if (!existing) {
      byLabel.set(item.label, item);
    } else if (item.id.startsWith("artifact:") && !existing.id.startsWith("artifact:")) {
      // Prefer canonical artifact document over mirror entity
      byLabel.set(item.label, item);
    }
  }

  return Array.from(byLabel.values());
}

const FLOW_COLUMN = 230;
const FLOW_ROW = 52;

function flowBranchPath(x1: number, y1: number, x2: number, y2: number) {
  const dx = Math.max(0, x2 - x1);
  return `M ${x1} ${y1} C ${x1 + dx * 0.45} ${y1}, ${x2 - dx * 0.45} ${y2}, ${x2} ${y2}`;
}

function flowCurveY(x1: number, y1: number, x2: number, y2: number, atX: number) {
  const dx = Math.max(1, x2 - x1);
  const c1x = x1 + dx * 0.45;
  const c2x = x2 - dx * 0.45;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 18; i += 1) {
    const t = (lo + hi) / 2;
    const mt = 1 - t;
    const x = mt * mt * mt * x1 + 3 * mt * mt * t * c1x + 3 * mt * t * t * c2x + t * t * t * x2;
    if (x < atX) lo = t;
    else hi = t;
  }
  const t = (lo + hi) / 2;
  const mt = 1 - t;
  return mt * mt * mt * y1 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t * t * t * y2;
}

function inkWidth(text: string) {
  return text.length * 6.7;
}

function nodeCaption(node: WebNode) {
  if (node.isGroup) return node.label;
  return `${node.label}${node.amount ? ` · $${node.amount}` : ""}`;
}

type InkBox = { x1: number; y1: number; x2: number; y2: number };

function boxesOverlap(a: InkBox, b: InkBox) {
  return a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
}

function nodeInkBox(node: WebNode): InkBox {
  const radius = node.isRoot ? 14 : 12;
  const labelX = (node.isRoot ? 11.5 : 8) + 8;
  return {
    x1: node.x - radius,
    y1: node.y - 16,
    x2: node.x + labelX + inkWidth(nodeCaption(node)) + 8,
    y2: node.y + 16,
  };
}

function ghostInkBox(x: number, y: number, caption: string): InkBox {
  return {
    x1: x - 18,
    y1: y - 20,
    x2: x + 22 + inkWidth(caption) + 8,
    y2: y + 18,
  };
}

function suggestionCaption(item: { label: string; amount: string | null }) {
  return `${item.label}${item.amount ? ` · $${item.amount}` : ""}`;
}

function clearFanSlots(ax: number, ay: number, ghostX: number, columnX: number, childYs: number[]) {
  const ordered = [...childYs].sort((a, b) => a - b);
  if (ordered.length === 0) {
    return [-FLOW_ROW, 0, FLOW_ROW].map((offset) => ay + offset);
  }
  const slots: number[] = [];
  const top = ordered[0] - FLOW_ROW;
  const bottom = ordered[ordered.length - 1] + FLOW_ROW;
  let runStart: number | null = null;
  let runEnd = top;
  for (let y = top; y <= bottom; y += 2) {
    const yAtColumn = flowCurveY(ax, ay, ghostX, y, columnX);
    const gap = ordered.reduce((best, childY) => Math.min(best, Math.abs(childY - yAtColumn)), Infinity);
    if (gap > 18) {
      if (runStart === null) runStart = y;
      runEnd = y;
    } else if (runStart !== null) {
      slots.push((runStart + runEnd) / 2);
      runStart = null;
    }
  }
  if (runStart !== null) slots.push((runStart + runEnd) / 2);
  const inFan = slots.filter((y) => y >= ordered[0] - FLOW_ROW * 0.65 && y <= ordered[ordered.length - 1] + FLOW_ROW * 0.65);
  return inFan.length > 0 ? inFan : slots;
}

function layoutSuggestionFan<T extends { id: string; anchorId: string; label: string; amount: string | null }>(
  items: T[],
  treeNodes: WebNode[],
  rootId: string | null
): Array<T & { x: number; y: number }> {
  const byAnchor = new Map<string, T[]>();
  for (const item of items) {
    const list = byAnchor.get(item.anchorId) || [];
    list.push(item);
    byAnchor.set(item.anchorId, list);
  }

  const occupied = treeNodes.map(nodeInkBox);
  const placed: Array<T & { x: number; y: number }> = [];

  for (const [anchorId, group] of byAnchor) {
    const anchor = treeNodes.find((node) => node.id === anchorId) || (rootId ? treeNodes.find((node) => node.id === rootId) : undefined);
    const ax = anchor?.x ?? 0;
    const ay = anchor?.y ?? 0;
    const anchorKey = anchor?.id || anchorId;
    const children = treeNodes.filter((node) => node.parentId === anchorKey).sort((a, b) => a.y - b.y);
    const columnX = children[0]?.x ?? ax + FLOW_COLUMN;
    const labelEdge = children.reduce((max, child) => Math.max(max, nodeInkBox(child).x2), columnX);
    // Sit on the same outward fan as this anchor's children, just past their labels.
    // +470 left a distant straight column; +188 painted over the first-tier labels.
    const ghostX = Math.max(ax + FLOW_COLUMN + 132, labelEdge + 22);
    const slots = clearFanSlots(ax, ay, ghostX, columnX, children.map((child) => child.y));
    let slotIndex = 0;

    group.forEach((item, index) => {
      const caption = suggestionCaption(item);
      let chosenX = ghostX;
      let chosenY = ay + (index - (group.length - 1) / 2) * FLOW_ROW;
      let found = false;

      for (let cursor = slotIndex; cursor < slots.length; cursor += 1) {
        const y = slots[cursor];
        if (placed.some((node) => Math.abs(node.y - y) < FLOW_ROW * 0.75 && Math.abs(node.x - ghostX) < 180)) continue;
        for (const extra of [0, 24, 48]) {
          const x = ghostX + extra;
          const box = ghostInkBox(x, y, caption);
          const blocked =
            occupied.some((ink) => boxesOverlap(box, ink)) ||
            placed.some((node) => boxesOverlap(box, ghostInkBox(node.x, node.y, suggestionCaption(node))));
          if (!blocked) {
            chosenX = x;
            chosenY = y;
            slotIndex = cursor + 1;
            found = true;
            occupied.push(box);
            break;
          }
        }
        if (found) break;
      }

      if (!found) {
        const top = children[0]?.y ?? ay;
        chosenY = top - FLOW_ROW * (group.length - index);
        occupied.push(ghostInkBox(chosenX, chosenY, caption));
      }
      placed.push({ ...item, x: chosenX, y: chosenY });
    });
  }

  return placed;
}

export function RadialWebGraph({
  nodes,
  edges,
  initialRootId,
  onRootChange,
  selected,
  onSelect,
  suggestions = [],
  inferredLinks = [],
  openSuggestionId = null,
  onOpenSuggestion,
  onApproveSuggestion,
  onDismissSuggestion,
  onDrawnIds,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  initialRootId?: string | null;
  onRootChange?: (newRoot: string) => void;
  selected: string | null;
  onSelect: (id: string) => void;
  suggestions?: AgentSuggestion[];
  inferredLinks?: InferredLink[];
  openSuggestionId?: string | null;
  onOpenSuggestion?: (id: string) => void;
  onApproveSuggestion?: (id: string) => void;
  onDismissSuggestion?: (id: string) => void;
  onDrawnIds?: (ids: string[]) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ w: 1000, h: 700 });
  const [camera, setCamera] = useState({ x: -280, y: 0, k: 1 });
  const isPanning = useRef<{ startX: number; startY: number; camX: number; camY: number } | null>(null);
  const heldSelection = useRef<string | null | undefined>(undefined);
  const inferredRef = useRef<InferredLink[]>(inferredLinks);
  inferredRef.current = inferredLinks;

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

  // Root ID & Upstream Flow Trail
  const [rootId, setRootId] = useState<string>("");
  const [upstreamTrail, setUpstreamTrail] = useState<string[]>([]);
  const [spotlightMode, setSpotlightMode] = useState<boolean>(true);

  // Root Candidate list for the root picker dropdown: always includes current root
  const rootCandidates = useMemo(() => {
    const map = new Map<string, GraphNode>();
    if (rootId && nodeMap.has(rootId)) {
      map.set(rootId, nodeMap.get(rootId)!);
    }
    for (const n of nodes) {
      if (n.type === "DECISION" || recordCode(n).startsWith("JE") || n.id.includes("cell:D3") || recordCode(n) === "D3") {
        map.set(n.id, n);
      }
    }
    const sorted = [...nodes].sort((a, b) => (adjacency.get(b.id)?.size || 0) - (adjacency.get(a.id)?.size || 0));
    for (const n of sorted.slice(0, 15)) {
      map.set(n.id, n);
    }
    return Array.from(map.values());
  }, [nodes, rootId, nodeMap, adjacency]);

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
  // The root reset and the selection reveal both write the tree. Skip one reveal
  // pass so it grafts onto the freshly reset tree instead of the stale one.
  const skipReveal = useRef(false);

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
      rootId,
      upstreamTrail,
    };
    historyStack.current = [snap, ...historyStack.current].slice(0, 40);
  }, [visibleIds, openSet, selected, treeParents, treeChildren, groupNodes, rootId, upstreamTrail]);

  // Atomic Flow Initializer (Synchronizes visibleIds, treeParents, treeChildren without intermediate lag)
  const initializeFlowForRoot = useCallback(
    (targetRoot: string, trail: string[]) => {
      const upstreamLabels = new Set<string>();
      for (const uid of trail) {
        const l = nodeMap.get(uid)?.label;
        if (l) upstreamLabels.add(l);
      }

      // Exclude upstream ancestors so the path forward strictly points right
      const distinctNeighbors = getDistinctNeighbors(targetRoot, adjacency, nodeMap, undefined, upstreamLabels);
      const newVisible = new Set<string>([targetRoot]);
      const newParents = new Map<string, string>();
      const newChildren = new Map<string, string[]>();
      const newGroups = new Map<string, any>();
      const newOpen = new Set<string>([targetRoot]);

      const byType = new Map<string, GraphNode[]>();
      for (const node of distinctNeighbors) {
        const t = node.type || "OTHER";
        const list = byType.get(t) || [];
        list.push(node);
        byType.set(t, list);
      }

      const rootKids: string[] = [];
      byType.forEach((items, type) => {
        if (items.length > 3) {
          const gid = `group:${targetRoot}:${type}:0`;
          newGroups.set(gid, {
            id: gid,
            type,
            label: `${items.length} ${pluralize(type)}`,
            count: items.length,
            members: items,
            parentId: targetRoot,
          });
          newVisible.add(gid);
          newParents.set(gid, targetRoot);
          rootKids.push(gid);
        } else {
          for (const item of items) {
            newVisible.add(item.id);
            newParents.set(item.id, targetRoot);
            rootKids.push(item.id);
          }
        }
      });

      newChildren.set(targetRoot, rootKids);
      graftInferred(inferredRef.current, targetRoot, nodeMap, newVisible, newParents, newChildren, newGroups, newOpen);
      setVisibleIds(newVisible);
      setTreeParents(newParents);
      setTreeChildren(newChildren);
      setGroupNodes(newGroups);
      setOpenSet(newOpen);
      setCrossLinks([]);
    },
    [adjacency, nodeMap]
  );

  // Handle Root Change & Maintain Flow Lineage
  const changeRoot = useCallback(
    (newRoot: string) => {
      if (!newRoot || !nodeMap.has(newRoot)) return;
      if (newRoot === rootId) return;

      pushHistory();

      let nextTrail = upstreamTrail;
      const idx = upstreamTrail.indexOf(newRoot);
      if (idx !== -1) {
        nextTrail = upstreamTrail.slice(0, idx);
      } else if (rootId) {
        nextTrail = [...upstreamTrail, rootId];
      } else {
        nextTrail = [];
      }

      setUpstreamTrail(nextTrail);
      setRootId(newRoot);
      initializeFlowForRoot(newRoot, nextTrail);
      onSelect(newRoot);
      onRootChange?.(newRoot);
    },
    [rootId, nodeMap, upstreamTrail, initializeFlowForRoot, onSelect, onRootChange, pushHistory]
  );

  // Synchronize initialRootId prop from parent
  useEffect(() => {
    if (!initialRootId || !nodeMap.has(initialRootId)) return;
    if (initialRootId === rootId) return;
    changeRoot(initialRootId);
  }, [initialRootId, rootId, nodeMap, changeRoot]);

  // Initial Root Determination
  useEffect(() => {
    if (nodes.length === 0 || rootId) return;

    if (initialRootId && nodeMap.has(initialRootId)) {
      setRootId(initialRootId);
      return;
    }

    const decision = nodes.find((n) => n.type === "DECISION" || recordCode(n).startsWith("JE"));
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

  // Handle Undo / Back
  const handleBack = useCallback(() => {
    if (historyStack.current.length === 0) return;
    const [prev, ...rest] = historyStack.current;
    historyStack.current = rest;

    if (prev.rootId && prev.rootId !== rootId) {
      setRootId(prev.rootId);
      onRootChange?.(prev.rootId);
    }
    if (prev.upstreamTrail) {
      setUpstreamTrail(prev.upstreamTrail);
    }
    setVisibleIds(new Set(prev.visibleIds));
    setOpenSet(new Set(prev.openIds));
    setTreeParents(new Map(prev.parentMap));
    setTreeChildren(new Map(prev.childrenMap));
    setGroupNodes(new Map(prev.groupMap));
    setCrossLinks([]);
    if (prev.selectedId) onSelect(prev.selectedId);
  }, [onSelect, rootId, onRootChange]);

  // Upstream Ancestor Nodes (Rendered to the Left of the Root)
  const upstreamNodes = useMemo(() => {
    return upstreamTrail.map((uid, index) => {
      const n = nodeMap.get(uid);
      const stepsFromRoot = upstreamTrail.length - index;
      const x = -stepsFromRoot * 210;
      const y = 0;
      return {
        id: uid,
        label: n?.label || uid,
        documentId: n?.document_id && n.document_id !== (n.label || uid) ? n.document_id : "",
        type: n?.type || "DOCUMENT",
        amount: n?.amount || null,
        x,
        y,
      };
    });
  }, [upstreamTrail, nodeMap]);

  // Reset to initial root view
  const handleResetToRoot = useCallback(() => {
    if (!rootId) return;
    pushHistory();
    setUpstreamTrail([]);
    const distinctNeighbors = getDistinctNeighbors(rootId, adjacency, nodeMap);

    const newVisible = new Set<string>([rootId]);
    const newParents = new Map<string, string>();
    const newChildren = new Map<string, string[]>();
    const newGroups = new Map<string, any>();
    const newOpen = new Set<string>([rootId]);

    const byType = new Map<string, GraphNode[]>();
    for (const node of distinctNeighbors) {
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

  // Populate Forward Flow when rootId or upstreamTrail changes
  useEffect(() => {
    if (!rootId || !nodeMap.has(rootId)) return;
    skipReveal.current = true;
    initializeFlowForRoot(rootId, upstreamTrail);
  }, [rootId, upstreamTrail, initializeFlowForRoot]);

  const treeRef = useRef({ visibleIds, treeParents, treeChildren, groupNodes, openSet, rootId });
  treeRef.current = { visibleIds, treeParents, treeChildren, groupNodes, openSet, rootId };

  useEffect(() => {
    if (!inferredLinks.length || !rootId || !nodeMap.has(rootId)) return;
    const tree = treeRef.current;
    const vis = new Set(tree.visibleIds);
    const parents = new Map(tree.treeParents);
    const children = new Map(tree.treeChildren);
    const groups = new Map(tree.groupNodes);
    const open = new Set(tree.openSet);
    const needs =
      inferredLinks.some((link) => nodeMap.has(link.target) && !vis.has(link.target)) ||
      inferredLinks.some((link) => vis.has(link.source) && vis.has(link.target) && parents.get(link.target) !== link.source);
    if (!needs) return;
    graftInferred(inferredLinks, rootId, nodeMap, vis, parents, children, groups, open);
    setVisibleIds(vis);
    setTreeParents(parents);
    setTreeChildren(children);
    setGroupNodes(groups);
    setOpenSet(open);
  }, [inferredLinks, rootId, nodeMap]);

  useEffect(() => {
    if (!onDrawnIds) return;
    const ids = [...visibleIds].filter((id) => !id.startsWith("group:") && nodeMap.has(id)).sort();
    onDrawnIds(ids);
  }, [visibleIds, nodeMap, onDrawnIds]);

  // Bring a selection that sits outside the expanded web onto the tree,
  // so spotlight has a real path instead of dimming every visible node.
  // Upstream origins and shelf alternatives already have a place on screen.
  useEffect(() => {
    if (skipReveal.current) {
      skipReveal.current = false;
      return;
    }
    if (!selected || !rootId || visibleIds.size === 0 || !nodeMap.has(selected)) return;
    if (suggestions.some((item) => item.id === selected)) return;
    if (visibleIds.has(selected)) return;
    if (upstreamTrail.includes(selected)) return;

    const visibleGroup = (nodeId: string) => groupIdForMember(nodeId, groupNodes, visibleIds);

    const path = pathFromVisible(selected, visibleIds, groupNodes, adjacency, nodeMap);
    if (!path) {
      if (!initialRootId && rootId !== selected) {
        onRootChange?.(selected);
        setRootId(selected);
      }
      return;
    }

    const vis = new Set(visibleIds);
    const parents = new Map(treeParents);
    const children = new Map(treeChildren);
    const groups = new Map(groupNodes);
    const open = new Set(openSet);

    const anchorGroup = visibleGroup(path[0]);
    if (anchorGroup) pullGroupMember(anchorGroup, path[0], vis, parents, children, groups, open, rootId);

    for (let index = 0; index < path.length - 1; index += 1) {
      const parentId = path[index];
      const childId = path[index + 1];
      if (!vis.has(parentId) || vis.has(childId)) continue;
      vis.add(childId);
      parents.set(childId, parentId);
      const kids = [...(children.get(parentId) || [])];
      kids.push(childId);
      children.set(parentId, kids);
      open.add(parentId);
    }

    if (!vis.has(selected)) return;
    setVisibleIds(vis);
    setTreeParents(parents);
    setTreeChildren(children);
    setGroupNodes(groups);
    setOpenSet(open);
    setCrossLinks([]);
  }, [
    selected,
    rootId,
    initialRootId,
    visibleIds,
    treeParents,
    treeChildren,
    groupNodes,
    openSet,
    adjacency,
    nodeMap,
    onRootChange,
    upstreamTrail,
    suggestions,
  ]);

  // Stable Directional Tree Layout (Anchored Root, Zero Vertical Shift on Expand)
  const treeNodes = useMemo(() => {
    if (!rootId || visibleIds.size === 0) return [];

    const COLUMN_GAP = 230;
    const ROW_GAP = 52;

    const coords = new Map<string, Point>();

    // 1. Root is rock-solid anchored at (0, 0)
    coords.set(rootId, { x: 0, y: 0 });

    // 2. Root's direct children (Tier 1) - centered around y = 0
    const rootKids = (treeChildren.get(rootId) || []).filter((k) => visibleIds.has(k));
    const numRootKids = rootKids.length;
    rootKids.forEach((kidId, idx) => {
      const y = (idx - (numRootKids - 1) / 2) * ROW_GAP;
      coords.set(kidId, { x: COLUMN_GAP, y });
    });

    // 3. Deeper tiers: Each branch expands outward to the right from its parent
    let currentDepthParents = rootKids.filter((id) => openSet.has(id));
    let depth = 2;

    while (currentDepthParents.length > 0) {
      const nextDepthParents: string[] = [];
      const colX = depth * COLUMN_GAP;
      let lastAllocatedY = -Infinity;

      // Sort parents from top to bottom by their stable Y
      currentDepthParents.sort((a, b) => (coords.get(a)?.y || 0) - (coords.get(b)?.y || 0));

      for (const pId of currentDepthParents) {
        const pCoord = coords.get(pId) || { x: (depth - 1) * COLUMN_GAP, y: 0 };
        const kids = (treeChildren.get(pId) || []).filter((k) => visibleIds.has(k));
        if (kids.length === 0) continue;

        const numKids = kids.length;
        // Children branch directly outward from parent, centered around parent's Y
        let idealStartY = pCoord.y - ((numKids - 1) / 2) * ROW_GAP;

        // Prevent collision with previous sibling branches
        if (idealStartY < lastAllocatedY + ROW_GAP) {
          idealStartY = lastAllocatedY + ROW_GAP;
        }

        kids.forEach((kId, i) => {
          const kidY = idealStartY + i * ROW_GAP;
          coords.set(kId, { x: colX, y: kidY });
          lastAllocatedY = kidY;
          if (openSet.has(kId)) {
            nextDepthParents.push(kId);
          }
        });
      }

      currentDepthParents = nextDepthParents;
      depth++;
    }

    const result: WebNode[] = [];
    for (const id of Array.from(visibleIds)) {
      const pt = coords.get(id);
      if (!pt) continue; // Skip orphaned/unpositioned nodes so they NEVER pile up at (0, 0)!
      const isRoot = id === rootId;
      const d = Math.round(pt.x / COLUMN_GAP);

      const isGroup = id.startsWith("group:");
      const groupData = groupNodes.get(id);
      const nodeData = nodeMap.get(id);

      result.push({
        id,
        type: isGroup ? groupData?.type || "GROUP" : nodeData?.type || "DOCUMENT",
        label: isGroup ? groupData?.label || "Group" : nodeData?.label || id,
        documentId: isGroup ? "" : nodeData?.document_id || "",
        amount: isGroup ? null : nodeData?.amount || null,
        title: isGroup ? `Group of ${groupData?.count} ${groupData?.type}` : nodeData?.title || nodeData?.label,
        depth: d,
        x: pt.x,
        y: pt.y,
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
      // Group ids are not records. Expanding one must not become the selection,
      // or spotlight dims the whole web and the inspector has nothing to show.
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

      onSelect(node.id);

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

      // 4. Unopened Node: Check distinct neighbors (exclude upstream ancestors so flow points right)
      const upstreamLabels = new Set<string>();
      for (const uid of upstreamTrail) {
        const l = nodeMap.get(uid)?.label;
        if (l) upstreamLabels.add(l);
      }
      const distinctNeighbors = getDistinctNeighbors(node.id, adjacency, nodeMap, visibleIds, upstreamLabels);

      // 5. If all distinct neighbors already on screen: draw cross-links and show toast
      if (distinctNeighbors.length === 0) {
        const rawLinked = Array.from(adjacency.get(node.id) || []).filter((id) => id !== node.id && visibleIds.has(id));
        if (rawLinked.length > 0) {
          setCrossLinks(rawLinked.map((target) => ({ from: node.id, to: target })));
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
      for (const item of distinctNeighbors) {
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
      upstreamTrail,
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

  // Active animated coordinates & spawning state for outward flow from parent
  const [animPositions, setAnimPositions] = useState<Map<string, Point>>(new Map());
  const [spawningIds, setSpawningIds] = useState<Set<string>>(new Set());
  const prevCoordsRef = useRef<Map<string, Point>>(new Map());

  useEffect(() => {
    const prevMap = prevCoordsRef.current;
    const initialMap = new Map<string, Point>();
    const newlyAdded = new Set<string>();

    for (const n of treeNodes) {
      if (prevMap.has(n.id)) {
        initialMap.set(n.id, prevMap.get(n.id)!);
      } else {
        // Brand new node: spawn right at parent position (flowing outward from parent)
        const pId = n.parentId || rootId;
        const pPt = prevMap.get(pId) || { x: 0, y: 0 };
        initialMap.set(n.id, { x: pPt.x, y: pPt.y });
        newlyAdded.add(n.id);
      }
    }

    setAnimPositions(initialMap);

    if (newlyAdded.size > 0) {
      setSpawningIds(newlyAdded);
      // Wait for next frame so DOM mounts at parent coordinates first, then glides to target
      const rAF = requestAnimationFrame(() => {
        const targetMap = new Map<string, Point>();
        for (const n of treeNodes) {
          targetMap.set(n.id, { x: n.x, y: n.y });
        }
        setAnimPositions(targetMap);
        setSpawningIds(new Set());
      });

      const nextTargetMap = new Map<string, Point>();
      for (const n of treeNodes) nextTargetMap.set(n.id, { x: n.x, y: n.y });
      prevCoordsRef.current = nextTargetMap;

      return () => cancelAnimationFrame(rAF);
    } else {
      const nextTargetMap = new Map<string, Point>();
      for (const n of treeNodes) nextTargetMap.set(n.id, { x: n.x, y: n.y });
      prevCoordsRef.current = nextTargetMap;
    }
  }, [treeNodes, rootId]);

  const posMap = animPositions;

  // Spotlight trail along the expanded tree. Null means "do not dim":
  // the selection is missing from this view, so graying every node would hide the web.
  const activeTrail = useMemo(() => {
    if (!selected || !spotlightMode || !visibleIds.has(selected)) return null;
    const trail = new Set<string>();
    const seen = new Set<string>();
    let cur: string | null = selected;
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      trail.add(cur);
      cur = treeParents.get(cur) || null;
    }
    for (const kid of treeChildren.get(selected) || []) {
      if (visibleIds.has(kid)) trail.add(kid);
    }
    return trail;
  }, [selected, spotlightMode, visibleIds, treeParents, treeChildren]);

  const centerView = useCallback(() => {
    if (treeNodes.length === 0) {
      heldSelection.current = selected;
      setCamera({ x: 0, y: 0, k: 1 });
      return;
    }
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const node of treeNodes) {
      minX = Math.min(minX, node.x - 20);
      maxX = Math.max(maxX, node.x + 150);
      minY = Math.min(minY, node.y - 22);
      maxY = Math.max(maxY, node.y + 22);
    }
    for (const node of upstreamNodes) {
      minX = Math.min(minX, node.x - 24);
      maxX = Math.max(maxX, node.x + 160);
      minY = Math.min(minY, node.y - 28);
      maxY = Math.max(maxY, node.y + 22);
    }
    const framedGhosts = layoutSuggestionFan(
      suggestions.filter((item) => !visibleIds.has(item.id)),
      treeNodes,
      rootId
    );
    for (const ghost of framedGhosts) {
      const caption = suggestionCaption(ghost);
      minX = Math.min(minX, ghost.x - 24);
      maxX = Math.max(maxX, ghost.x + 28 + inkWidth(caption));
      minY = Math.min(minY, ghost.y - 24);
      maxY = Math.max(maxY, ghost.y + 22);
    }
    const pad = 28;
    const top = 132;
    const right = 400;
    const bottom = pad;
    const availW = Math.max(220, dimensions.w - right - pad * 2);
    const availH = Math.max(220, dimensions.h - top - bottom);
    const contentW = Math.max(48, maxX - minX);
    const contentH = Math.max(48, maxY - minY);
    const k = Math.min(1.35, availW / contentW, availH / contentH);
    const midX = (minX + maxX) / 2;
    const midY = (minY + maxY) / 2;
    const visibleCenterX = pad + availW / 2;
    const visibleCenterY = top + availH / 2;
    heldSelection.current = selected;
    setCamera({
      k,
      x: visibleCenterX - dimensions.w / 2 - midX * k,
      y: visibleCenterY - dimensions.h / 2 - midY * k,
    });
  }, [treeNodes, upstreamNodes, dimensions.w, dimensions.h, selected, suggestions, visibleIds, rootId]);

  const inferredPairs = useMemo(() => {
    const pairs = new Set<string>();
    for (const link of inferredLinks) {
      pairs.add(`${link.source}|${link.target}`);
      pairs.add(`${link.target}|${link.source}`);
    }
    return pairs;
  }, [inferredLinks]);

  const ghostLayout = useMemo(() => {
    return layoutSuggestionFan(
      suggestions.filter((item) => !visibleIds.has(item.id)),
      treeNodes,
      rootId
    );
  }, [suggestions, visibleIds, treeNodes, rootId]);

  // Stable, Calm Camera Framing: keeps root anchored on the left without jarring whip-pans.
  // A Center click holds this camera until the selection changes.
  useEffect(() => {
    if (heldSelection.current !== undefined && heldSelection.current === selected) return;
    heldSelection.current = undefined;
    if (isPanning.current) return;

    // Anchor root at 250px from stage left if upstream origin exists, else 130px
    const baseAnchorX = upstreamTrail.length > 0 ? 250 : 130;
    const maxVisibleX = Math.max(
      treeNodes.reduce((max, n) => Math.max(max, n.x), 0),
      ghostLayout.reduce((max, node) => Math.max(max, node.x + 220), 0)
    );
    const visibleWidth = Math.max(340, dimensions.w - 440); // 440px reserved for drawer + margins

    let targetCamX = baseAnchorX - dimensions.w / 2;

    // Only gently ease left if deep columns would be occluded by the drawer
    const treeScreenRight = maxVisibleX * camera.k + baseAnchorX;
    if (treeScreenRight > visibleWidth) {
      targetCamX -= (treeScreenRight - visibleWidth + 40);
    }

    setCamera((c) => {
      if (Math.abs(c.x - targetCamX) < 4 && Math.abs(c.y) < 4) return c;
      return { ...c, x: targetCamX, y: 0 };
    });
  }, [treeNodes, ghostLayout, upstreamTrail.length, dimensions.w, camera.k, selected]);

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
          k: Math.max(0.12, Math.min(2.0, Number((c.k * factor).toFixed(3)))),
        }));
      }}
    >
      <button className="center-view" onClick={centerView} title="Center and show every node">
        Center
      </button>

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
              changeRoot(newRoot);
            }}
          >
            {rootCandidates.map((cand) => (
              <option key={cand.id} value={cand.id}>
                {clipLabel(cand.label, 42)}
                {cand.document_id && cand.document_id !== cand.label ? ` · ${cand.document_id}` : ""}
                {cand.amount ? ` ($${cand.amount})` : ""} · {cand.type}
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
        <defs>
          <marker
            id="upstream-flow-arrow"
            viewBox="0 0 10 10"
            refX="7"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#FA6610" />
          </marker>
        </defs>
        <g
          style={{
            transform: `translate3d(${dimensions.w / 2 + camera.x}px, ${dimensions.h / 2 + camera.y}px, 0) scale(${camera.k})`,
            transition: isPanning.current ? "none" : "transform 260ms cubic-bezier(0.23, 1, 0.32, 1)",
          }}
        >
          {/* 0. Upstream Flow Connectors (From Left Origin into Current Root) */}
          {upstreamNodes.map((un, idx) => {
            const nextX = idx < upstreamNodes.length - 1 ? upstreamNodes[idx + 1].x : 0;
            const nextY = idx < upstreamNodes.length - 1 ? upstreamNodes[idx + 1].y : 0;
            return (
              <line
                key={`upstream-flow-${un.id}`}
                x1={un.x}
                y1={un.y}
                x2={nextX - 16}
                y2={nextY}
                stroke="#FA6610"
                strokeWidth={1.8}
                strokeDasharray="4 4"
                opacity={0.75}
                markerEnd="url(#upstream-flow-arrow)"
              />
            );
          })}

          {/* 0b. Upstream Origin Nodes on the Left */}
          {upstreamNodes.map((un) => {
            const isSelected = selected === un.id;
            const isHovered = hoveredId === un.id;
            const color = getNodeColor(un.type);
            return (
              <g
                key={`upstream-node-${un.id}`}
                className="radial-node-group"
                style={{
                  transform: `translate3d(${un.x}px, ${un.y}px, 0)`,
                  cursor: "pointer",
                  transition: "transform 240ms cubic-bezier(0.23, 1, 0.32, 1)",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  changeRoot(un.id);
                }}
                onPointerEnter={() => setHoveredId(un.id)}
                onPointerLeave={() => setHoveredId(null)}
              >
                {/* Origin indicator badge above node */}
                <text
                  x={0}
                  y={-17}
                  textAnchor="middle"
                  fill="#FA6610"
                  fontSize={9}
                  fontWeight={700}
                  letterSpacing="0.06em"
                  fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
                >
                  ↶ ORIGIN
                </text>

                {/* Dashed outer boundary ring */}
                <circle
                  r={15}
                  fill="none"
                  stroke="#FA6610"
                  strokeWidth={1.2}
                  strokeDasharray="3 3"
                  opacity={0.6}
                />

                {/* Node Circle (Fills on hover, unfills when not hovered) */}
                <circle
                  r={10}
                  fill={isHovered ? color : "#FFFFFF"}
                  stroke={isSelected || isHovered ? "#0D0D0C" : color}
                  strokeWidth={isSelected ? 2.5 : 2}
                  style={{ transition: "fill 140ms ease, stroke 140ms ease" }}
                />
                {!isHovered && <circle r={3} fill={color} pointerEvents="none" />}

                {/* Label */}
                <text
                  x={16}
                  y={4}
                  textAnchor="start"
                  className="radial-node-label"
                  fill="#0D0D0C"
                  fontWeight={isSelected ? 700 : 600}
                  fontSize={11.5}
                  fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
                >
                  <title>{un.label}</title>
                  {clipLabel(un.label, un.amount ? 28 : 36)} {un.amount ? `· $${un.amount}` : ""}
                </text>
                {un.documentId ? (
                  <text
                    x={16}
                    y={16}
                    textAnchor="start"
                    fill="#6B655F"
                    fontWeight={500}
                    fontSize={9}
                    fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
                  >
                    {un.documentId}
                  </text>
                ) : null}
              </g>
            );
          })}
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
                stroke="rgba(13, 13, 12, 0.04)"
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
              const isHovered = hoveredId === n.id || hoveredId === n.parentId;
              const isContradiction = edgeType === "CONTRADICTS";
              const isSupports = edgeType === "RECONCILES_WITH" || edgeType === "POTENTIALLY_SUPPORTS";
              const isAgent = inferredPairs.has(`${n.parentId}|${n.id}`);
              const isDashed = n.isGroup || edgeType === "POTENTIALLY_SUPPORTS";

              const isSpawning = spawningIds.has(n.id);
              const onTrail = Boolean(activeTrail?.has(n.parentId!) && activeTrail.has(n.id));
              const isEdgeInFocus = !activeTrail || onTrail;
              const strokeColor = isContradiction
                ? "#FA6610"
                : isSupports
                ? "#10B981"
                : isSelected || isHovered || onTrail
                ? "#0D0D0C"
                : "rgba(13, 13, 12, 0.24)";

              const strokeW = isSelected || isHovered || onTrail ? 2.2 : isContradiction || isSupports ? 1.8 : 1.3;
              const edgeOpacity = isSpawning
                ? 0
                : !isEdgeInFocus
                ? 0.16
                : isSelected || isHovered
                ? 1.0
                : isContradiction || isSupports
                ? 0.95
                : 0.65;

              const pathD = flowBranchPath(p.x, p.y, c.x, c.y);

              const midX = (p.x + c.x) / 2;
              const midY = (p.y + c.y) / 2;
              return (
                <g key={`edge-${n.parentId}-${n.id}`}>
                  <path
                    className={isAgent ? "flow-branch-edge agent-inferred-edge" : "flow-branch-edge"}
                    d={pathD}
                    fill="none"
                    stroke={isAgent ? "#FA6610" : strokeColor}
                    strokeWidth={isAgent ? 2.2 : strokeW}
                    strokeDasharray={isAgent ? undefined : isDashed ? "4 4" : "none"}
                    opacity={isAgent ? 1 : edgeOpacity}
                    style={{
                      transition: isSpawning
                        ? "none"
                        : "d 260ms cubic-bezier(0.23, 1, 0.32, 1), opacity 200ms cubic-bezier(0.23, 1, 0.32, 1), stroke 0.15s ease",
                    }}
                  />
                  {isAgent ? (
                    <text x={midX} y={midY - 8} textAnchor="middle" className="agent-inferred-label">
                      Agent inferred
                    </text>
                  ) : null}
                </g>
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
                strokeWidth={1.5}
                strokeDasharray="4 4"
                opacity={0.85}
              />
            );
          })}

          {ghostLayout.map((ghost) => {
            const anchor = posMap.get(ghost.anchorId) || treeNodes.find((node) => node.id === ghost.anchorId);
            const ax = anchor && "x" in anchor ? anchor.x : ghost.x - FLOW_COLUMN;
            const ay = anchor && "y" in anchor ? anchor.y : ghost.y;
            const ring = ghost.edgeType === "CONTRADICTS" ? "#FA6610" : ghost.edgeType === "AMBIGUOUS" ? "#8B5CF6" : "#64748B";
            return (
              <path
                key={`ghost-edge-${ghost.id}`}
                className="agent-ghost-edge"
                d={flowBranchPath(ax, ay, ghost.x, ghost.y)}
                fill="none"
                stroke={ring}
                strokeWidth={1.5}
                strokeDasharray="4 4"
                opacity={0.88}
                pointerEvents="none"
              />
            );
          })}

          {/* 4. Nodes (All Visible, High Contrast, Smooth Staggered Transitions) */}
          {treeNodes.map((n) => {
            const pos = posMap.get(n.id) || { x: n.x, y: n.y };
            const isSpawning = spawningIds.has(n.id);
            const isSelected = selected === n.id;
            const isHovered = hoveredId === n.id;
            const color = getNodeColor(n.type);
            const r = n.isRoot ? 11.5 : 8;

            // Missing selections do not dim the web. A real trail dims everything off the path.
            const isNodeInFocus = !activeTrail || activeTrail.has(n.id);
            // Fill node on hover, unfill when not hovered
            const isFilled = isHovered;
            const fill = isFilled ? color : "#FFFFFF";
            const stroke = isHovered ? "#0D0D0C" : isSelected ? "#0D0D0C" : color;
            const strokeWidth = isSelected ? 2.5 : isHovered ? 2.2 : 1.8;

            const labelX = r + 8;
            const labelY = 3.5;

            return (
              <g
                key={n.id}
                className="radial-node-group"
                style={{
                  transform: `translate3d(${pos.x}px, ${pos.y}px, 0)`,
                  opacity: isSpawning ? 0 : isNodeInFocus ? 1 : 0.22,
                  transition: isSpawning
                    ? "none"
                    : "transform 260ms cubic-bezier(0.23, 1, 0.32, 1), opacity 200ms cubic-bezier(0.23, 1, 0.32, 1)",
                  cursor: "pointer",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  handleNodeClick(n);
                }}
                onPointerEnter={() => setHoveredId(n.id)}
                onPointerLeave={() => setHoveredId(null)}
              >
                {/* Selected Halo Ring */}
                {isSelected && (
                  <circle
                    r={r + 5}
                    fill="none"
                    stroke="#0D0D0C"
                    strokeWidth={2}
                    opacity={0.3}
                  />
                )}

                {/* Group Node: 3 Offset Hollow Circles */}
                {n.isGroup ? (
                  <g className="group-circles">
                    <circle cx={-3} cy={-2} r={r} fill={isHovered ? color : "#FFFFFF"} stroke={color} strokeWidth={1.6} style={{ transition: "fill 140ms ease" }} />
                    <circle cx={0} cy={0} r={r} fill={isHovered ? color : "#FFFFFF"} stroke={color} strokeWidth={1.6} style={{ transition: "fill 140ms ease" }} />
                    <circle cx={3} cy={2} r={r} fill={isHovered ? color : "#FFFFFF"} stroke={isHovered ? "#0D0D0C" : color} strokeWidth={1.6} style={{ transition: "fill 140ms ease" }} />
                  </g>
                ) : (
                  /* Standard Node Circle */
                  <circle
                    r={r}
                    fill={fill}
                    stroke={stroke}
                    strokeWidth={strokeWidth}
                    style={{ transition: "fill 140ms ease, stroke 140ms ease" }}
                  />
                )}

                {/* Inner Dot for Unfilled Nodes */}
                {!isFilled && !n.isGroup && (
                  <circle r={n.isRoot ? 3.5 : 2.5} fill={color} pointerEvents="none" />
                )}

                {/* High Contrast Crisp Label */}
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor="start"
                  className="radial-node-label"
                  fill="#0D0D0C"
                  fontWeight={isSelected || isHovered ? 700 : n.isRoot ? 600 : 500}
                  fontSize={n.isRoot ? 12 : 11.5}
                  fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
                >
                  <title>{n.label}</title>
                  {n.isGroup
                    ? n.label
                    : `${clipLabel(n.label, n.amount ? 28 : 36)}${n.amount ? ` · $${n.amount}` : ""}`}
                </text>
                {!n.isGroup && n.documentId && n.documentId !== n.label ? (
                  <text
                    x={labelX}
                    y={labelY + 13}
                    textAnchor="start"
                    fill="#6B655F"
                    fontWeight={500}
                    fontSize={9}
                    fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
                  >
                    {n.documentId}
                  </text>
                ) : null}
              </g>
            );
          })}

          {ghostLayout.map((ghost) => {
            const picked = selected === ghost.id || openSuggestionId === ghost.id;
            const ring = ghost.edgeType === "CONTRADICTS" ? "#FA6610" : ghost.edgeType === "AMBIGUOUS" ? "#8B5CF6" : "#64748B";
            const named = nodeMap.get(ghost.id);
            const ghostCode = named?.document_id && named.document_id !== ghost.label ? named.document_id : "";
            const caption = `${clipLabel(ghost.label, 42)}${ghostCode ? ` · ${ghostCode}` : ""}${ghost.amount ? ` · $${ghost.amount}` : ""}`;
            const hitWidth = Math.max(168, caption.length * 6.6 + 78);
            return (
              <g key={`ghost-${ghost.id}`} className="agent-ghost" transform={`translate(${ghost.x} ${ghost.y})`}>
                <g
                  className="agent-ghost-hit"
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(ghost.id);
                    onOpenSuggestion?.(ghost.id);
                  }}
                >
                  <rect x={-20} y={-18} width={hitWidth} height={32} rx={16} fill="transparent" />
                  <circle r={16} fill={picked ? "rgba(250, 102, 16, 0.08)" : "none"} stroke={ring} strokeWidth={picked ? 2 : 1.4} strokeDasharray="4 3" className="suggestion-ring" />
                  <circle r={5} fill="#FFFFFF" stroke={ring} strokeWidth={1.4} />
                  <text x={20} y={-2} textAnchor="start" className="agent-ghost-kicker">
                    Suggested
                  </text>
                  <text x={20} y={12} textAnchor="start" className="radial-node-label agent-ghost-label">
                    {caption}
                  </text>
                </g>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

function graftInferred(
  links: InferredLink[],
  rootId: string,
  nodeMap: Map<string, GraphNode>,
  vis: Set<string>,
  parents: Map<string, string>,
  children: Map<string, string[]>,
  groups: Map<string, { members?: GraphNode[]; parentId?: string; type?: string; label?: string; count?: number; id?: string }>,
  open: Set<string>
) {
  for (const link of links) {
    if (!nodeMap.has(link.target)) continue;
    const groupId = groupIdForMember(link.target, groups, vis);
    if (groupId) pullGroupMember(groupId, link.target, vis, parents, children, groups, open, rootId);
    if (!vis.has(link.target)) {
      const parentId = vis.has(link.source) ? link.source : rootId;
      vis.add(link.target);
      parents.set(link.target, parentId);
      const kids = [...(children.get(parentId) || [])];
      if (!kids.includes(link.target)) kids.push(link.target);
      children.set(parentId, kids);
      open.add(parentId);
    }
    if (vis.has(link.source) && parents.get(link.target) !== link.source) {
      const oldParent = parents.get(link.target);
      if (oldParent) children.set(oldParent, (children.get(oldParent) || []).filter((kid) => kid !== link.target));
      parents.set(link.target, link.source);
      const kids = [...(children.get(link.source) || [])];
      if (!kids.includes(link.target)) kids.push(link.target);
      children.set(link.source, kids);
      open.add(link.source);
    }
  }
}

function groupIdForMember(
  nodeId: string,
  groups: Map<string, { members?: { id: string }[] }>,
  visible: Set<string>
): string | null {
  for (const [groupId, group] of groups) {
    if (!visible.has(groupId)) continue;
    if (group.members?.some((member) => member.id === nodeId)) return groupId;
  }
  return null;
}

function pathFromVisible(
  target: string,
  visible: Set<string>,
  groups: Map<string, { members?: { id: string }[] }>,
  adjacency: Map<string, Set<string>>,
  nodeMap: Map<string, GraphNode>
): string[] | null {
  if (visible.has(target) || groupIdForMember(target, groups, visible)) return [target];

  const previous = new Map<string, string>();
  const queue = [target];
  const seen = new Set<string>([target]);
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const next of adjacency.get(current) || []) {
      if (seen.has(next) || !nodeMap.has(next)) continue;
      seen.add(next);
      previous.set(next, current);
      if (visible.has(next) || groupIdForMember(next, groups, visible)) {
        const path = [next];
        let cursor = next;
        while (cursor !== target) {
          const step = previous.get(cursor);
          if (!step) return null;
          path.push(step);
          cursor = step;
        }
        return path;
      }
      queue.push(next);
    }
  }
  return null;
}

function pullGroupMember(
  groupId: string,
  memberId: string,
  vis: Set<string>,
  parents: Map<string, string>,
  children: Map<string, string[]>,
  groups: Map<string, any>,
  open: Set<string>,
  rootId: string
) {
  const group = groups.get(groupId);
  if (!group) return;
  const parentId = group.parentId || rootId;
  const remaining = ((group.members || []) as GraphNode[]).filter((member) => member.id !== memberId);
  const kids = [...(children.get(parentId) || [])].filter((kid) => kid !== groupId);

  vis.delete(groupId);
  parents.delete(groupId);
  groups.delete(groupId);

  if (!vis.has(memberId)) {
    vis.add(memberId);
    parents.set(memberId, parentId);
    kids.push(memberId);
  }

  if (remaining.length > 3) {
    groups.set(groupId, {
      ...group,
      members: remaining,
      count: remaining.length,
      label: `${remaining.length} ${pluralize(group.type || "")}`,
    });
    vis.add(groupId);
    parents.set(groupId, parentId);
    kids.push(groupId);
  } else {
    for (const item of remaining) {
      if (vis.has(item.id)) continue;
      vis.add(item.id);
      parents.set(item.id, parentId);
      kids.push(item.id);
    }
  }

  children.set(parentId, kids);
  open.add(parentId);
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
