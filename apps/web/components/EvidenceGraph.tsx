"use client";

import type { GraphEdge, GraphNode } from "@/lib/api";
import type { AgentSuggestion, InferredLink } from "@/lib/investigate";
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
  suggestions,
  inferredLinks,
  openSuggestionId,
  onOpenSuggestion,
  onApproveSuggestion,
  onDismissSuggestion,
  onDrawnIds,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  focused?: string | null;
  viewMode?: "web" | "atom";
  rootId?: string | null;
  onRootChange?: (id: string) => void;
  onSelect: (id: string) => void;
  suggestions?: AgentSuggestion[];
  inferredLinks?: InferredLink[];
  openSuggestionId?: string | null;
  onOpenSuggestion?: (id: string) => void;
  onApproveSuggestion?: (id: string) => void;
  onDismissSuggestion?: (id: string) => void;
  onDrawnIds?: (ids: string[]) => void;
}) {
  if (viewMode === "atom") {
    return (
      <ObsidianGraph
        nodes={nodes}
        edges={edges}
        selected={selected}
        focused={focused}
        onSelect={onSelect}
        suggestions={suggestions}
        inferredLinks={inferredLinks}
        openSuggestionId={openSuggestionId}
        onOpenSuggestion={onOpenSuggestion}
        onApproveSuggestion={onApproveSuggestion}
        onDismissSuggestion={onDismissSuggestion}
        onDrawnIds={onDrawnIds}
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
      suggestions={suggestions}
      inferredLinks={inferredLinks}
      openSuggestionId={openSuggestionId}
      onOpenSuggestion={onOpenSuggestion}
      onApproveSuggestion={onApproveSuggestion}
      onDismissSuggestion={onDismissSuggestion}
      onDrawnIds={onDrawnIds}
    />
  );
}
