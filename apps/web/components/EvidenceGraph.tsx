"use client";

import type { GraphEdge, GraphNode } from "@/lib/api";
import { ObsidianGraph } from "@/components/ObsidianGraph";
import { RadialWebGraph } from "@/components/RadialWebGraph";

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
  viewMode?: "web" | "atom";
  rootId?: string | null;
  onRootChange?: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  if (viewMode === "atom") {
    return (
      <ObsidianGraph
        nodes={nodes}
        edges={edges}
        selected={selected}
        focused={focused}
        onSelect={onSelect}
      />
    );
  }
  return (
    <RadialWebGraph
      nodes={nodes}
      edges={edges}
      initialRootId={rootId}
      onRootChange={onRootChange}
      selected={selected}
      onSelect={onSelect}
    />
  );
}
