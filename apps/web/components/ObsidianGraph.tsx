"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Node3D = {
  id: string;
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  r: number;
  degree: number;
  label: string;
  group: string;
  color: string;
  type: string;
  amount: string | null;
  redacted: boolean;
  // Projected screen coordinates
  projX: number;
  projY: number;
  projZ: number;
  projScale: number;
};

type EdgeIndex = {
  sourceIndex: number;
  targetIndex: number;
  sourceId: string;
  targetId: string;
  type: string;
  inferred: boolean;
};

const PALETTE = [
  "#FA6610", // Comma orange
  "#10B981", // Emerald green
  "#2563EB", // Tech blue
  "#8B5CF6", // Purple
  "#D97706", // Amber
  "#06B6D4", // Cyan
  "#EC4899", // Pink
  "#4F46E5", // Indigo
];

export function ObsidianGraph({
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
  const canvas = useRef<HTMLCanvasElement>(null);

  const bodies = useRef<Node3D[]>([]);
  const edgeList = useRef<EdgeIndex[]>([]);
  const neighbors = useRef<Map<string, Set<string>>>(new Map());

  // 3D Camera & Turntable Rotation
  const camera = useRef({
    x: 0,
    y: 0,
    k: 1.0,
    rotY: 0, // 360 yaw rotation
    rotX: 0.22, // Slight tilt for 3D perspective
    autoRotate: true,
  });

  const simAlpha = useRef<number>(1.0);
  const aim = useRef<number | null>(null);

  useEffect(() => {
    if (!selected) {
      aim.current = null;
      return;
    }
    const body = bodies.current.find((node) => node.id === selected);
    if (!body) return;
    aim.current = Math.PI - Math.atan2(body.x, body.z);
  }, [selected]);
  const isDraggingNode = useRef<string | null>(null);
  const isRotating = useRef<{ startX: number; startY: number; startRotY: number; startRotX: number; startPanX: number; startPanY: number; isPan: boolean } | null>(null);
  const pointerDownPos = useRef<{ x: number; y: number } | null>(null);

  const [hover, setHover] = useState<string | null>(null);

  const signature = useMemo(() => `${nodes.length}:${edges.length}:${nodes.map((n) => n.id).join("|")}`, [nodes, edges]);

  // 1. Initialize 3D Atom Sphere
  useEffect(() => {
    const degreeMap = new Map<string, number>();
    const linked = new Map<string, Set<string>>();

    for (const edge of edges) {
      degreeMap.set(edge.source, (degreeMap.get(edge.source) || 0) + 1);
      degreeMap.set(edge.target, (degreeMap.get(edge.target) || 0) + 1);
      if (!linked.has(edge.source)) linked.set(edge.source, new Set());
      if (!linked.has(edge.target)) linked.set(edge.target, new Set());
      linked.get(edge.source)!.add(edge.target);
      linked.get(edge.target)!.add(edge.source);
    }
    neighbors.current = linked;

    const nodeIndexMap = new Map<string, number>();
    const placed: Node3D[] = [];

    // Distribute nodes in a 3D atomic structure: dense central nucleus + concentric orbital shells
    const count = nodes.length;
    const phi = (1 + Math.sqrt(5)) / 2; // Golden ratio

    nodes.forEach((node, i) => {
      const g = node.group || "General";
      const deg = degreeMap.get(node.id) || 0;

      // Clean minimalistic node sizes
      // Nucleus hubs (Decisions, Key POs, Vendors): ~7-10px radius
      // Inner valence docs: ~5-6.5px radius
      // Outer peripheral nodes: ~3.8-4.5px radius
      const isNucleus = deg >= 5 || node.type === "DECISION" || node.label.startsWith("JE") || node.group === "Vendors";
      const isInner = !isNucleus && (deg >= 2 || node.type === "PURCHASE_ORDER" || node.type === "CONTRACT");
      const baseRadius = isNucleus ? 7.5 + Math.min(3.0, deg * 0.35) : isInner ? 5.2 + Math.min(1.8, deg * 0.25) : 3.8;

      // Stratified atomic orbital shell radii
      let targetRadius = 220;
      if (isNucleus) {
        targetRadius = 28 + (i % 4) * 10; // Dense central nucleus
      } else if (isInner) {
        targetRadius = 115 + (i % 5) * 12; // Valence shell 1
      } else {
        targetRadius = 195 + (i % 6) * 14; // Valence shell 2
      }

      // Spherical distribution around the atom's shell
      const yNorm = 1 - (i / Math.max(count - 1, 1)) * 2; // -1 to 1
      const radiusAtY = Math.sqrt(Math.max(0, 1 - yNorm * yNorm));
      const theta = 2 * Math.PI * i / phi;

      const x = Math.cos(theta) * radiusAtY * targetRadius;
      const y = yNorm * targetRadius * 0.80;
      const z = Math.sin(theta) * radiusAtY * targetRadius;

      placed.push({
        id: node.id,
        x: x + (Math.random() - 0.5) * 14,
        y: y + (Math.random() - 0.5) * 14,
        z: z + (Math.random() - 0.5) * 14,
        vx: 0,
        vy: 0,
        vz: 0,
        r: baseRadius,
        degree: deg,
        label: node.label,
        group: g,
        color: getNodeColor(node),
        type: node.type,
        amount: node.amount,
        redacted: Boolean(node.redacted),
        projX: 0,
        projY: 0,
        projZ: 0,
        projScale: 1,
      });
      nodeIndexMap.set(node.id, placed.length - 1);
    });

    bodies.current = placed;

    const edgesIndexed: EdgeIndex[] = [];
    for (const edge of edges) {
      const sIdx = nodeIndexMap.get(edge.source);
      const tIdx = nodeIndexMap.get(edge.target);
      if (sIdx !== undefined && tIdx !== undefined) {
        edgesIndexed.push({
          sourceIndex: sIdx,
          targetIndex: tIdx,
          sourceId: edge.source,
          targetId: edge.target,
          type: edge.type,
          inferred: Boolean(edge.inferred),
        });
      }
    }
    edgeList.current = edgesIndexed;

    const el = frame.current;
    if (el) {
      camera.current.x = el.clientWidth / 2;
      camera.current.y = el.clientHeight / 2;
      camera.current.k = el.clientWidth < 900 ? 0.85 : 1.05;
    }

    simAlpha.current = 1.0;
  }, [signature, nodes, edges]);

  // 2. Continuous 3D Physics & Slow 360° Rotation Animation Loop
  useEffect(() => {
    const element = frame.current;
    const surface = canvas.current;
    if (!element || !surface) return;

    let frameId = 0;

    const tick = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      const ratio = window.devicePixelRatio || 1;

      if (surface.width !== Math.floor(width * ratio) || surface.height !== Math.floor(height * ratio)) {
        surface.width = Math.floor(width * ratio);
        surface.height = Math.floor(height * ratio);
      }

      const ctx = surface.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

      if (aim.current != null && !isRotating.current && !isDraggingNode.current) {
        const delta = Math.atan2(Math.sin(aim.current - camera.current.rotY), Math.cos(aim.current - camera.current.rotY));
        if (Math.abs(delta) < 0.02) aim.current = null;
        else camera.current.rotY += delta * 0.12;
      } else if (camera.current.autoRotate && !isRotating.current && !isDraggingNode.current && !selected) {
        camera.current.rotY += 0.0016; // ~1 full rotation every 65 seconds
      }

      // Step 3D force simulation
      if (simAlpha.current > 0.002) {
        step3DPhysics(bodies.current, edgeList.current, simAlpha.current, isDraggingNode.current);
        simAlpha.current *= 0.993;
      }

      // Project 3D nodes to 2D screen coordinates
      project3DTo2D(bodies.current, camera.current);

      // Paint 3D atom graph with depth ordering
      paint3DAtomGraph(
        ctx,
        width,
        height,
        bodies.current,
        neighbors.current,
        edgeList.current,
        camera.current,
        hover,
        selected
      );

      frameId = window.requestAnimationFrame(tick);
    };

    frameId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frameId);
  }, [signature, hover, selected]);

  // 3. Trackpad Pinch-to-Zoom (Non-passive so page NEVER zooms)
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();

      const rect = el.getBoundingClientRect();
      const cam = camera.current;

      const zoomIntensity = event.ctrlKey ? 0.012 : 0.0018;
      const delta = -event.deltaY * zoomIntensity;
      const factor = Math.exp(delta);
      const nextK = Math.min(3.5, Math.max(0.25, cam.k * factor));

      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      cam.x = px - ((px - cam.x) * nextK) / cam.k;
      cam.y = py - ((py - cam.y) * nextK) / cam.k;
      cam.k = nextK;
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Hit test on projected 2D screen coordinates
  const findNodeAtScreen = useCallback((clientX: number, clientY: number) => {
    const rect = canvas.current?.getBoundingClientRect();
    if (!rect) return null;
    const sx = clientX - rect.left;
    const sy = clientY - rect.top;

    // Check front-to-back (highest projScale first)
    const sorted = [...bodies.current].sort((a, b) => b.projScale - a.projScale);
    for (const body of sorted) {
      const hitRadius = Math.max(body.r * body.projScale + 8, 18);
      const d = Math.hypot(body.projX - sx, body.projY - sy);
      if (d < hitRadius) {
        return body;
      }
    }
    return null;
  }, []);

  const handleZoom = (factor: number) => {
    const el = frame.current;
    if (!el) return;
    const cx = el.clientWidth / 2;
    const cy = el.clientHeight / 2;
    const nextK = Math.min(3.5, Math.max(0.25, camera.current.k * factor));
    camera.current.x = cx - ((cx - camera.current.x) * nextK) / camera.current.k;
    camera.current.y = cy - ((cy - camera.current.y) * nextK) / camera.current.k;
    camera.current.k = nextK;
  };

  const handleResetView = () => {
    const el = frame.current;
    if (!el) return;
    aim.current = null;
    const rotY = 0;
    const rotX = 0.22;
    const cosY = Math.cos(rotY);
    const sinY = Math.sin(rotY);
    const cosX = Math.cos(rotX);
    const sinX = Math.sin(rotX);
    const focal = 800;
    let maxX = 40;
    let maxY = 40;
    for (const node of bodies.current) {
      const x1 = node.x * cosY + node.z * sinY;
      const z1 = -node.x * sinY + node.z * cosY;
      const y2 = node.y * cosX - z1 * sinX;
      const z2 = node.y * sinX + z1 * cosX;
      const scale = focal / (focal + z2);
      const pad = (node.r + 22) * scale;
      maxX = Math.max(maxX, Math.abs(x1) * scale + pad);
      maxY = Math.max(maxY, Math.abs(y2) * scale + pad);
    }
    const width = el.clientWidth;
    const height = el.clientHeight;
    const fit = Math.min((width - 96) / (2 * maxX), (height - 96) / (2 * maxY));
    camera.current.rotY = rotY;
    camera.current.rotX = rotX;
    camera.current.k = Math.max(0.2, Math.min(2.2, fit));
    camera.current.x = width / 2;
    camera.current.y = height / 2;
  };

  return (
    <div className="graph obsidian" ref={frame} role="group" aria-label="3D Atom knowledge graph">
      <canvas
        ref={canvas}
        aria-label="3D rotating atom graph"
        onPointerDown={(event) => {
          pointerDownPos.current = { x: event.clientX, y: event.clientY };
          const hitNode = findNodeAtScreen(event.clientX, event.clientY);
          if (hitNode) {
            isDraggingNode.current = hitNode.id;
            simAlpha.current = Math.max(simAlpha.current, 0.45);
            onSelect(hitNode.id);
          } else {
            // Right-click or Shift-drag to pan; Left-drag to rotate turntable in 3D
            const isPan = event.button === 2 || event.shiftKey;
            isRotating.current = {
              startX: event.clientX,
              startY: event.clientY,
              startRotY: camera.current.rotY,
              startRotX: camera.current.rotX,
              startPanX: camera.current.x,
              startPanY: camera.current.y,
              isPan,
            };
          }
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (isDraggingNode.current) {
            const rect = canvas.current?.getBoundingClientRect();
            if (!rect) return;
            const sx = event.clientX - rect.left;
            const sy = event.clientY - rect.top;
            const cam = camera.current;

            // Unproject screen 2D position back into 3D world plane
            const target = bodies.current.find((b) => b.id === isDraggingNode.current);
            if (target) {
              const cosY = Math.cos(-cam.rotY);
              const sinY = Math.sin(-cam.rotY);
              const dx = (sx - cam.x) / cam.k;
              const dy = (sy - cam.y) / cam.k;

              target.x = dx * cosY;
              target.z = dx * sinY;
              target.y = dy;
              target.vx = 0;
              target.vy = 0;
              target.vz = 0;
              simAlpha.current = Math.max(simAlpha.current, 0.25);
            }
            return;
          }

          if (isRotating.current) {
            const dx = event.clientX - isRotating.current.startX;
            const dy = event.clientY - isRotating.current.startY;

            if (isRotating.current.isPan) {
              camera.current.x = isRotating.current.startPanX + dx;
              camera.current.y = isRotating.current.startPanY + dy;
            } else {
              // 3D turntable rotation
              camera.current.rotY = isRotating.current.startRotY + dx * 0.006;
              camera.current.rotX = Math.max(-1.2, Math.min(1.2, isRotating.current.startRotX + dy * 0.005));
            }
            return;
          }

          const hitNode = findNodeAtScreen(event.clientX, event.clientY);
          setHover(hitNode ? hitNode.id : null);
        }}
        onPointerLeave={() => {
          setHover(null);
        }}
        onPointerUp={(event) => {
          if (pointerDownPos.current) {
            const dist = Math.hypot(event.clientX - pointerDownPos.current.x, event.clientY - pointerDownPos.current.y);
            if (dist < 6 && !isDraggingNode.current) {
              const hitNode = findNodeAtScreen(event.clientX, event.clientY);
              if (!hitNode && selected) {
                // Clicked on empty space: deselect so rotation resumes!
                onSelect("");
              }
            }
          }
          if (isDraggingNode.current) {
            simAlpha.current = Math.max(simAlpha.current, 0.2);
            isDraggingNode.current = null;
          }
          isRotating.current = null;
        }}
        onContextMenu={(e) => e.preventDefault()}
        onDoubleClick={handleResetView}
      />

      {/* Floating Canvas Controls */}
      <div className="canvas-zoom-controls">
        <button className="center-view" onClick={handleResetView} title="Center and show every node">
          Center
        </button>
        <button onClick={() => handleZoom(1.2)} title="Zoom in">+</button>
        <button onClick={() => handleZoom(0.8)} title="Zoom out">−</button>
      </div>
    </div>
  );
}

// -------------------------------------------------------------
// 3D FORCE SIMULATION (Coulomb Repulsion + Spring Links in 3D)
// -------------------------------------------------------------
function step3DPhysics(
  nodes: Node3D[],
  edges: EdgeIndex[],
  alpha: number,
  draggedId: string | null
) {
  const n = nodes.length;
  if (n === 0) return;

  // 1. 3D Coulomb Repulsion (keeps nodes pleasantly separated)
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let dz = b.z - a.z;
      let distSq = dx * dx + dy * dy + dz * dz;
      if (distSq < 1) distSq = 1;
      const dist = Math.sqrt(distSq);

      if (dist > 320) continue;

      const force = (540 / (distSq + 140)) * alpha;

      const minD = a.r + b.r + 24; // Clean collision radius
      const overlap = minD - dist;
      const push = overlap > 0 ? overlap * 0.20 * alpha : 0;

      const fx = (dx / dist) * (force + push);
      const fy = (dy / dist) * (force + push);
      const fz = (dz / dist) * (force + push);

      if (a.id !== draggedId) {
        a.vx -= fx;
        a.vy -= fy;
        a.vz -= fz;
      }
      if (b.id !== draggedId) {
        b.vx += fx;
        b.vy += fy;
        b.vz += fz;
      }
    }
  }

  // 2. 3D Spring Links (Hooke's Law between connected documents)
  const targetLinkDist = 72;
  for (let e = 0; e < edges.length; e++) {
    const edge = edges[e];
    const a = nodes[edge.sourceIndex];
    const b = nodes[edge.targetIndex];
    if (!a || !b) continue;

    let dx = b.x - a.x;
    let dy = b.y - a.y;
    let dz = b.z - a.z;
    let dist = Math.hypot(dx, dy, dz) || 0.1;
    const diff = (dist - targetLinkDist) / dist;
    const springStrength = 0.045 * alpha;

    const fx = dx * diff * springStrength;
    const fy = dy * diff * springStrength;
    const fz = dz * diff * springStrength;

    if (a.id !== draggedId) {
      a.vx += fx;
      a.vy += fy;
      a.vz += fz;
    }
    if (b.id !== draggedId) {
      b.vx -= fx;
      b.vy -= fy;
      b.vz -= fz;
    }
  }

  // 3. Atomic Shell Gravitation: pulls nucleus to the core and keeps electron shells at cohesive orbital distances
  const damping = 0.86;

  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    if (a.id === draggedId) continue;

    const distFromOrigin = Math.hypot(a.x, a.y, a.z) || 0.1;
    const isNucleus = a.degree >= 5 || a.type === "DECISION" || a.label.startsWith("JE") || a.group === "Vendors";
    const isInner = !isNucleus && (a.degree >= 2 || a.type === "PURCHASE_ORDER" || a.type === "CONTRACT");

    // Cohesive shell distances
    const idealR = isNucleus ? 40 : isInner ? 135 : 215;
    const shellDiff = (distFromOrigin - idealR) / distFromOrigin;

    // Pull toward its orbital shell
    const shellPull = shellDiff * (isNucleus ? 0.0038 : 0.0018) * alpha;

    a.vx -= a.x * shellPull;
    a.vy -= a.y * shellPull;
    a.vz -= a.z * shellPull;

    a.vx *= damping;
    a.vy *= damping;
    a.vz *= damping;

    const speed = Math.hypot(a.vx, a.vy, a.vz);
    const maxSpeed = 14;
    if (speed > maxSpeed) {
      a.vx = (a.vx / speed) * maxSpeed;
      a.vy = (a.vy / speed) * maxSpeed;
      a.vz = (a.vz / speed) * maxSpeed;
    }

    a.x += a.vx;
    a.y += a.vy;
    a.z += a.vz;
  }
}

// -------------------------------------------------------------
// 3D PERSPECTIVE PROJECTION
// -------------------------------------------------------------
function project3DTo2D(
  nodes: Node3D[],
  cam: { x: number; y: number; k: number; rotY: number; rotX: number }
) {
  const cosY = Math.cos(cam.rotY);
  const sinY = Math.sin(cam.rotY);
  const cosX = Math.cos(cam.rotX);
  const sinX = Math.sin(cam.rotX);
  const focalLength = 800; // Immersive 3D depth perspective

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];

    // 1. Yaw rotation (Y-axis 360)
    const x1 = node.x * cosY + node.z * sinY;
    const z1 = -node.x * sinY + node.z * cosY;

    // 2. Pitch tilt (X-axis)
    const y2 = node.y * cosX - z1 * sinX;
    const z2 = node.y * sinX + z1 * cosX;

    // 3. Perspective scale factor based on depth z2
    const scale = focalLength / (focalLength + z2);

    node.projScale = scale;
    node.projZ = z2;
    node.projX = cam.x + x1 * scale * cam.k;
    node.projY = cam.y + y2 * scale * cam.k;
  }
}

// -------------------------------------------------------------
// 3D CANVAS RENDERING (Depth Shading & Single Tooltip HUD)
// -------------------------------------------------------------
function paint3DAtomGraph(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  nodes: Node3D[],
  neighbors: Map<string, Set<string>>,
  edges: EdgeIndex[],
  cam: { x: number; y: number; k: number },
  hoverId: string | null,
  selectedId: string | null
) {
  ctx.clearRect(0, 0, width, height);

  const selectedInView = Boolean(selectedId && nodes.some((node) => node.id === selectedId));
  const activeId = hoverId || (selectedInView ? selectedId : null);
  const nearSet = new Set<string>();
  if (activeId) {
    nearSet.add(activeId);
    for (const neighbor of neighbors.get(activeId) || []) {
      nearSet.add(neighbor);
    }
  }

  // 1. Draw Edges
  for (let i = 0; i < edges.length; i++) {
    const edge = edges[i];
    const a = nodes[edge.sourceIndex];
    const b = nodes[edge.targetIndex];
    if (!a || !b) continue;

    const isConnected = nearSet.has(a.id) && nearSet.has(b.id);
    const avgScale = (a.projScale + b.projScale) * 0.5;

    if (activeId) {
      // Faded look: only connections within the hovered/selected node group stay illuminated
      if (isConnected) {
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = edge.type === "CONTRADICTS" ? "#FA6610" : "rgba(13, 13, 12, 0.70)";
      } else {
        ctx.lineWidth = 0.8;
        ctx.strokeStyle = "rgba(13, 13, 12, 0.03)";
      }
    } else {
      const depthAlpha = Math.max(0.06, Math.min(0.35, (avgScale - 0.35) * 0.5));
      ctx.lineWidth = 1.0;
      ctx.strokeStyle = edge.type === "CONTRADICTS"
        ? `rgba(250, 102, 16, ${depthAlpha * 1.6})`
        : `rgba(13, 13, 12, ${depthAlpha})`;
    }

    ctx.beginPath();
    ctx.moveTo(a.projX, a.projY);
    ctx.lineTo(b.projX, b.projY);
    ctx.stroke();
  }

  // 2. Draw Clean Minimalistic Nodes (Sorted back-to-front by depth projZ)
  const sortedNodes = [...nodes].sort((a, b) => b.projZ - a.projZ);

  for (let i = 0; i < sortedNodes.length; i++) {
    const node = sortedNodes[i];
    const isSelected = node.id === selectedId;
    const isHovered = node.id === hoverId;
    const isLit = !activeId || nearSet.has(node.id);

    const renderR = Math.max(2.5, node.r * node.projScale * (isSelected ? 1.4 : isHovered ? 1.3 : 1.0));
    const depthAlpha = Math.max(0.4, Math.min(1.0, (node.projScale - 0.25) * 1.1));

    // Preserve the faded look for everything but the selected/hovered node group
    ctx.globalAlpha = isLit ? depthAlpha : 0.08;

    // Clean flat minimal circle like Obsidian
    ctx.fillStyle = node.color;
    ctx.beginPath();
    ctx.arc(node.projX, node.projY, renderR, 0, Math.PI * 2);
    ctx.fill();

    // Clean stroke on selected or hovered node
    if (isSelected || isHovered) {
      ctx.strokeStyle = "#0D0D0C";
      ctx.lineWidth = isSelected ? 2.0 : 1.5;
      ctx.stroke();
    } else if (isLit) {
      ctx.strokeStyle = "rgba(13, 13, 12, 0.12)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
  }

  // 3. Crisp single tooltip HUD for hovered or selected node
  ctx.globalAlpha = 1.0;
  const targetNode = nodes.find((n) => n.id === hoverId) || (selectedId ? nodes.find((n) => n.id === selectedId) : null);

  if (targetNode) {
    const labelText = targetNode.amount
      ? `${targetNode.label} · $${targetNode.amount}`
      : targetNode.label;

    ctx.font = "600 12px 'Saans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";

    const textWidth = ctx.measureText(labelText).width;
    const lx = targetNode.projX;
    const ly = targetNode.projY - targetNode.r * targetNode.projScale - 8;

    // Frosted Pill Container
    ctx.fillStyle = targetNode.id === selectedId ? "#0D0D0C" : "#FFFFFF";
    ctx.beginPath();
    ctx.roundRect(lx - textWidth / 2 - 8, ly - 18, textWidth + 16, 22, 6);
    ctx.fill();

    ctx.strokeStyle = targetNode.id === selectedId ? "#0D0D0C" : "rgba(231, 221, 210, 0.9)";
    ctx.lineWidth = 1.0;
    ctx.stroke();

    // Text Ink
    ctx.fillStyle = targetNode.id === selectedId ? "#FFFFFF" : "#0D0D0C";
    ctx.fillText(labelText, lx, ly - 4);
  }
}

function getNodeColor(node?: GraphNode | { group: string }): string {
  const type = "type" in (node || {}) ? (node as GraphNode).type : "";
  const group = node?.group || "";

  if (type === "DECISION" || group === "Transactions") return "#FA6610";
  if (type === "PURCHASE_ORDER") return "#10B981";
  if (group === "Vendors") return "#2563EB";
  if (group === "policy") return "#D97706";
  if (type === "CONTRACT") return "#8B5CF6";
  if ("redacted" in (node || {}) && (node as GraphNode).redacted) return "#94A3B8";

  let hash = 0;
  for (const char of group) hash = (hash * 33 + char.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
