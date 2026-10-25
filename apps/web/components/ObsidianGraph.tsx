"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

type Body = {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  gx: number;
  gy: number;
  r: number;
  label: string;
  group: string;
  color: string;
};

const PALETTE = ["#7aa2f7", "#f7768e", "#e0af68", "#9ece6a", "#bb9af7", "#7dcfff", "#ff9e64", "#73daca", "#c0caf5", "#f6c177", "#2ac3de", "#b4f9f8"];

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
  const bodies = useRef<Body[]>([]);
  const neighbors = useRef<Map<string, Set<string>>>(new Map());
  const view = useRef({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ id: string; dx: number; dy: number } | { pan: true; x: number; y: number; vx: number; vy: number } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [groups, setGroups] = useState<{ id: string; color: string; count: number }[]>([]);
  const signature = useMemo(() => nodes.map((node) => node.id).join("|"), [nodes]);

  useEffect(() => {
    const degree = new Map<string, number>();
    const linked = new Map<string, Set<string>>();
    for (const edge of edges) {
      degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
      if (!linked.has(edge.source)) linked.set(edge.source, new Set());
      if (!linked.has(edge.target)) linked.set(edge.target, new Set());
      linked.get(edge.source)?.add(edge.target);
      linked.get(edge.target)?.add(edge.source);
    }
    neighbors.current = linked;
    const grouped = new Map<string, GraphNode[]>();
    for (const node of nodes) {
      const group = node.group || "Other";
      const list = grouped.get(group) || [];
      list.push(node);
      grouped.set(group, list);
    }
    const names = [...grouped.keys()].sort();
    const placed: Body[] = [];
    names.forEach((group, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(names.length, 1) - Math.PI / 2;
      const reach = 280 + names.length * 18;
      const gx = Math.cos(angle) * reach;
      const gy = Math.sin(angle) * reach;
      const members = grouped.get(group) || [];
      members.forEach((node, member) => {
        const turn = (Math.PI * 2 * member) / Math.max(members.length, 1);
        const radius = 28 + Math.sqrt(members.length) * 16;
        placed.push({
          id: node.id,
          x: gx + Math.cos(turn) * radius,
          y: gy + Math.sin(turn) * radius,
          vx: 0,
          vy: 0,
          gx,
          gy,
          r: 3.2 + Math.sqrt(degree.get(node.id) || 0) * 1.35,
          label: node.label,
          group,
          color: groupColor(group),
        });
      });
    });
    bodies.current = placed;
    setGroups(names.map((id) => ({ id, color: groupColor(id), count: grouped.get(id)?.length || 0 })));
    const element = frame.current;
    if (element) {
      view.current = { x: element.clientWidth / 2, y: element.clientHeight / 2, k: element.clientWidth < 900 ? 0.55 : 0.72 };
    }
  }, [signature, edges, nodes]);

  useEffect(() => {
    const element = frame.current;
    const surface = canvas.current;
    if (!element || !surface) return;
    let frameId = 0;
    let settle = 0;
    const draw = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      const ratio = window.devicePixelRatio || 1;
      if (surface.width !== Math.floor(width * ratio) || surface.height !== Math.floor(height * ratio)) {
        surface.width = Math.floor(width * ratio);
        surface.height = Math.floor(height * ratio);
      }
      const context = surface.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (settle < 160 && !drag.current) {
        step(bodies.current, neighbors.current);
        settle += 1;
      }
      paint(context, width, height, bodies.current, neighbors.current, view.current, hover, selected, focused || null);
      frameId = window.requestAnimationFrame(draw);
    };
    frameId = window.requestAnimationFrame(draw);
    return () => window.cancelAnimationFrame(frameId);
  }, [signature, hover, selected, focused]);

  function worldPoint(event: { clientX: number; clientY: number }) {
    const rect = canvas.current?.getBoundingClientRect();
    const camera = view.current;
    if (!rect) return { x: 0, y: 0 };
    return { x: (event.clientX - rect.left - camera.x) / camera.k, y: (event.clientY - rect.top - camera.y) / camera.k };
  }

  function hit(event: { clientX: number; clientY: number }) {
    const point = worldPoint(event);
    let found: Body | null = null;
    let best = 14;
    for (const body of bodies.current) {
      const distance = Math.hypot(body.x - point.x, body.y - point.y);
      if (distance < body.r + 6 && distance < best) {
        best = distance;
        found = body;
      }
    }
    return found;
  }

  return (
    <div className="graph obsidian" ref={frame} role="group" aria-label="Knowledge graph">
      <canvas
        ref={canvas}
        aria-label="Interconnected company graph"
        onWheel={(event) => {
          event.preventDefault();
          const rect = canvas.current?.getBoundingClientRect();
          if (!rect) return;
          const camera = view.current;
          const next = Math.min(2.8, Math.max(0.18, camera.k * (event.deltaY > 0 ? 0.92 : 1.08)));
          const px = event.clientX - rect.left;
          const py = event.clientY - rect.top;
          view.current = { k: next, x: px - ((px - camera.x) * next) / camera.k, y: py - ((py - camera.y) * next) / camera.k };
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          if (current && "pan" in current) {
            view.current.x += event.clientX - current.x;
            view.current.y += event.clientY - current.y;
            drag.current = { pan: true, x: event.clientX, y: event.clientY, vx: view.current.x, vy: view.current.y };
            return;
          }
          if (current && "id" in current) {
            const point = worldPoint(event);
            const body = bodies.current.find((item) => item.id === current.id);
            if (body) {
              body.x = point.x + current.dx;
              body.y = point.y + current.dy;
              body.vx = 0;
              body.vy = 0;
            }
            return;
          }
          setHover(hit(event)?.id || null);
        }}
        onPointerDown={(event) => {
          const body = hit(event);
          const point = worldPoint(event);
          if (body) {
            drag.current = { id: body.id, dx: body.x - point.x, dy: body.y - point.y };
            onSelect(body.id);
          } else {
            drag.current = { pan: true, x: event.clientX, y: event.clientY, vx: view.current.x, vy: view.current.y };
          }
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
      />
      <div className="graph-key">
        {groups.map((group) => (
          <span key={group.id}><i style={{ background: group.color }} />{group.id} <small>{group.count}</small></span>
        ))}
      </div>
    </div>
  );
}

function step(items: Body[], linked: Map<string, Set<string>>) {
  const index = new Map(items.map((body, position) => [body.id, position]));
  const cell = 96;
  const buckets = new Map<string, number[]>();
  items.forEach((body, position) => {
    const key = `${Math.round(body.x / cell)}:${Math.round(body.y / cell)}`;
    const list = buckets.get(key) || [];
    list.push(position);
    buckets.set(key, list);
  });
  for (const [key, members] of buckets) {
    const [cx, cy] = key.split(":").map(Number);
    const nearby: number[] = [];
    for (let ox = -1; ox <= 1; ox += 1) {
      for (let oy = -1; oy <= 1; oy += 1) {
        const list = buckets.get(`${cx + ox}:${cy + oy}`);
        if (list) nearby.push(...list);
      }
    }
    for (const left of members) {
      for (const right of nearby) {
        if (right <= left) continue;
        const a = items[left];
        const b = items[right];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        const distance = Math.hypot(dx, dy) || 0.01;
        if (distance > 140) continue;
        const force = 80 / (distance * distance);
        dx = (dx / distance) * force;
        dy = (dy / distance) * force;
        a.vx += dx;
        a.vy += dy;
        b.vx -= dx;
        b.vy -= dy;
      }
    }
  }
  for (const [source, targets] of linked) {
    const from = index.get(source);
    if (from === undefined) continue;
    for (const target of targets) {
      const to = index.get(target);
      if (to === undefined || to <= from) continue;
      const a = items[from];
      const b = items[to];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.hypot(dx, dy) || 0.01;
      const pull = (distance - 54) * 0.012;
      const ux = (dx / distance) * pull;
      const uy = (dy / distance) * pull;
      a.vx += ux;
      a.vy += uy;
      b.vx -= ux;
      b.vy -= uy;
    }
  }
  for (const body of items) {
    body.vx += (body.gx - body.x) * 0.004;
    body.vy += (body.gy - body.y) * 0.004;
    body.vx *= 0.82;
    body.vy *= 0.82;
    body.x += Math.max(-8, Math.min(8, body.vx));
    body.y += Math.max(-8, Math.min(8, body.vy));
  }
}

function paint(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  items: Body[],
  linked: Map<string, Set<string>>,
  camera: { x: number; y: number; k: number },
  hover: string | null,
  selected: string | null,
  focused: string | null,
) {
  context.clearRect(0, 0, width, height);
  context.save();
  context.translate(camera.x, camera.y);
  context.scale(camera.k, camera.k);
  const focus = new Set<string>();
  if (focused) {
    for (const body of items) {
      if (body.group === focused) focus.add(body.id);
    }
    for (const id of [...focus]) {
      for (const next of linked.get(id) || []) focus.add(next);
    }
  }
  const active = hover || selected;
  const near = new Set<string>();
  if (active) {
    near.add(active);
    for (const next of linked.get(active) || []) near.add(next);
  }
  const byId = new Map(items.map((body) => [body.id, body]));
  context.lineWidth = 1;
  for (const [source, targets] of linked) {
    const from = byId.get(source);
    if (!from) continue;
    for (const target of targets) {
      if (target < source) continue;
      const to = byId.get(target);
      if (!to) continue;
      const lit = !active || near.has(source) || near.has(target);
      const grouped = !focused || focus.has(source) || focus.has(target);
      context.strokeStyle = lit && grouped ? "rgba(214, 222, 232, 0.55)" : "rgba(214, 222, 232, 0.06)";
      context.beginPath();
      context.moveTo(from.x, from.y);
      context.lineTo(to.x, to.y);
      context.stroke();
    }
  }
  for (const body of items) {
    const lit = (!active || near.has(body.id)) && (!focused || focus.has(body.id));
    context.globalAlpha = lit ? 1 : 0.12;
    context.fillStyle = body.color;
    context.beginPath();
    context.arc(body.x, body.y, body.id === selected ? body.r + 1.6 : body.r, 0, Math.PI * 2);
    context.fill();
    if (body.id === selected || body.id === hover) {
      context.strokeStyle = "#f4f7fb";
      context.lineWidth = 1.4;
      context.stroke();
    }
  }
  context.globalAlpha = 1;
  context.font = "11px Segoe UI, sans-serif";
  context.textAlign = "center";
  for (const body of items) {
    const show = body.id === hover || body.id === selected || (near.has(body.id) && camera.k > 0.9) || (items.length < 40 && camera.k > 0.7);
    if (!show) continue;
    if (focused && !focus.has(body.id)) continue;
    context.fillStyle = body.id === hover || body.id === selected ? "#f4f7fb" : "rgba(214, 222, 232, 0.78)";
    context.fillText(body.label, body.x, body.y - body.r - 5);
  }
  context.restore();
}

function groupColor(group: string) {
  if (group === "Vendors") return "#c0caf5";
  if (group === "Transactions") return "#7dcfff";
  if (group === "policy") return "#e0af68";
  let hash = 0;
  for (const char of group) hash = (hash * 33 + char.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
