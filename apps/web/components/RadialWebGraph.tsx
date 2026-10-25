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
  rootId?: string;
  upstreamTrail?: string[];
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
      if (n.type === "DECISION" || n.label.startsWith("JE") || n.id.includes("cell:D3") || n.label === "D3") {
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

  // Handle Root Change & Maintain Flow Lineage
  const changeRoot = useCallback(
    (newRoot: string) => {
      if (!newRoot || !nodeMap.has(newRoot)) return;
      if (newRoot === rootId) return;

      pushHistory();

      setUpstreamTrail((prevTrail) => {
        const idx = prevTrail.indexOf(newRoot);
        if (idx !== -1) {
          // Navigating backward along the trail
          return prevTrail.slice(0, idx);
        }
        // If moving forward from the current root:
        if (rootId) {
          return [...prevTrail, rootId];
        }
        return [];
      });

      setRootId(newRoot);
      onSelect(newRoot);
      onRootChange?.(newRoot);
    },
    [rootId, nodeMap, onSelect, onRootChange, pushHistory]
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
        type: n?.type || "DOCUMENT",
        amount: n?.amount || null,
        x,
        y,
      };
    });
  }, [upstreamTrail, nodeMap]);

  // Sibling Alternatives (Grouped at the Bottom Docked Shelf)
  const siblingAlternatives = useMemo(() => {
    if (upstreamTrail.length === 0) return [];
    const lastAncestorId = upstreamTrail[upstreamTrail.length - 1];
    const distinct = getDistinctNeighbors(lastAncestorId, adjacency, nodeMap);
    const currentRootLabel = nodeMap.get(rootId)?.label || rootId;
    const trailSet = new Set(upstreamTrail);
    return distinct.filter(
      (n) => n.id !== rootId && n.label !== currentRootLabel && !trailSet.has(n.id)
    );
  }, [upstreamTrail, rootId, adjacency, nodeMap]);

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

    const upstreamLabels = new Set<string>();
    for (const uid of upstreamTrail) {
      const l = nodeMap.get(uid)?.label;
      if (l) upstreamLabels.add(l);
    }

    // Exclude upstream ancestors so the path forward strictly points right
    const distinctNeighbors = getDistinctNeighbors(rootId, adjacency, nodeMap, undefined, upstreamLabels);
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
  }, [rootId, upstreamTrail, adjacency, nodeMap]);

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

  // Stable, Calm Camera Framing: keeps root anchored on the left without jarring whip-pans
  useEffect(() => {
    if (isPanning.current) return;

    // Anchor root at 250px from stage left if upstream origin exists, else 130px
    const baseAnchorX = upstreamTrail.length > 0 ? 250 : 130;
    const maxVisibleX = treeNodes.reduce((max, n) => Math.max(max, n.x), 0);
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
  }, [treeNodes, upstreamTrail.length, dimensions.w, camera.k]);

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
              changeRoot(newRoot);
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

                {/* Node Circle */}
                <circle
                  r={10}
                  fill="#FFFFFF"
                  stroke={isSelected || isHovered ? "#0D0D0C" : color}
                  strokeWidth={isSelected ? 2.5 : 2}
                />
                <circle r={3} fill={color} />

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
                  {un.label} {un.amount ? `· $${un.amount}` : ""}
                </text>
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
              const isDashed = n.isGroup || edgeType === "POTENTIALLY_SUPPORTS";

              const strokeColor = isContradiction
                ? "#FA6610"
                : isSupports
                ? "#10B981"
                : isSelected || isHovered
                ? "#0D0D0C"
                : "rgba(13, 13, 12, 0.24)";

              const strokeW = isSelected || isHovered ? 2.2 : isContradiction || isSupports ? 1.8 : 1.3;
              const edgeOpacity = isSelected || isHovered ? 1.0 : isContradiction || isSupports ? 0.95 : 0.65;

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
                  opacity={edgeOpacity}
                  style={{
                    transition: "stroke 0.15s ease, opacity 0.18s ease",
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
                strokeWidth={1.5}
                strokeDasharray="4 4"
                opacity={0.85}
              />
            );
          })}

          {/* 4. Nodes (All Visible, High Contrast, Smooth Staggered Transitions) */}
          {treeNodes.map((n, idx) => {
            const pos = posMap.get(n.id) || { x: n.x, y: n.y };
            const isSelected = selected === n.id;
            const isHovered = hoveredId === n.id;
            const color = getNodeColor(n.type);
            const r = n.isRoot ? 11.5 : 8;

            const isFilled = n.isRoot || n.isOpen;
            const fill = isFilled ? color : "#FFFFFF";
            const stroke = isSelected || isHovered ? "#0D0D0C" : color;
            const strokeWidth = isSelected ? 2.5 : isHovered ? 2.0 : isFilled ? 1.5 : 2.0;

            const labelX = r + 8;
            const labelY = 3.5;

            return (
              <g
                key={n.id}
                className="radial-node-group"
                style={{
                  transform: `translate3d(${pos.x}px, ${pos.y}px, 0)`,
                  opacity: 1,
                  transition: "transform 240ms cubic-bezier(0.23, 1, 0.32, 1), opacity 180ms cubic-bezier(0.23, 1, 0.32, 1)",
                  transitionDelay: `${Math.min(idx * 16, 160)}ms`,
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
                    <circle cx={-3} cy={-2} r={r} fill="#FFFFFF" stroke={color} strokeWidth={1.6} />
                    <circle cx={0} cy={0} r={r} fill="#FFFFFF" stroke={color} strokeWidth={1.6} />
                    <circle cx={3} cy={2} r={r} fill="#FFFFFF" stroke="#0D0D0C" strokeWidth={1.6} />
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

                {/* High Contrast Crisp Label */}
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor="start"
                  className="radial-node-label"
                  fill="#0D0D0C"
                  fontWeight={isSelected ? 700 : n.isRoot ? 600 : 500}
                  fontSize={n.isRoot ? 12 : 11.5}
                  fontFamily="'Saans', -apple-system, BlinkMacSystemFont, sans-serif"
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

      {/* 5. Alternative Siblings Shelf Docked at Bottom */}
      {siblingAlternatives.length > 0 && (
        <div className="flow-alt-shelf">
          <div className="shelf-title">
            <span>↶ From {nodeMap.get(upstreamTrail[upstreamTrail.length - 1])?.label || "Origin"}:</span>
          </div>
          <div className="shelf-chips">
            {siblingAlternatives.map((alt) => {
              const isSelected = selected === alt.id;
              return (
                <button
                  key={alt.id}
                  className={`shelf-chip ${isSelected ? "selected" : ""}`}
                  onClick={() => onSelect(alt.id)}
                  onDoubleClick={() => changeRoot(alt.id)}
                  title={`Click to inspect ${alt.label}. Double-click to set as flow root.`}
                >
                  <span className="chip-dot" style={{ background: getNodeColor(alt.type) }} />
                  <span className="chip-label">{alt.label}</span>
                  {alt.amount && <span className="chip-amt">${alt.amount}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
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
