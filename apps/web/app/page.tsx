"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DocumentPreview } from "@/components/DocumentPreview";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { InvestigationPanel } from "@/components/InvestigationPanel";
import { NodeSummary, useNodeSummary } from "@/components/NodeSummary";
import { IngestModal, type IngestJob } from "@/components/IngestModal";
import { API_URL, api, type ActorRow, type CompanyDocument, type CompanyGraph, type GraphEdge, type GraphNode } from "@/lib/api";
import {
  buildInvestigation,
  learnPrecedent,
  loadPrecedents,
  savePrecedents,
  type AgentSuggestion,
  type InvestigationStep,
  type InferredLink,
  type Precedent,
} from "@/lib/investigate";
import { parseGraphDeepLink } from "@/lib/deep-link";

function toUtcIso(date: Date) {
  return `${date.toISOString().slice(0, 19)}+00:00`;
}

export default function HomePage() {
  const [graph, setGraph] = useState<CompanyGraph | null>(null);
  const [allActors, setAllActors] = useState<ActorRow[]>([]);
  const [actorId, setActorId] = useState("actor-maya");
  const [asOf, setAsOf] = useState(() => toUtcIso(new Date()));
  const [mode, setMode] = useState("audit");
  const [viewMode, setViewMode] = useState<"web" | "atom">("web");
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [customRootId, setCustomRootId] = useState<string | null>(null);
  const [caseNodes, setCaseNodes] = useState<GraphNode[]>([]);
  const [caseEdges, setCaseEdges] = useState<GraphEdge[]>([]);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [pulse, setPulse] = useState(0);
  const [query, setQuery] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [drawnIds, setDrawnIds] = useState<string[]>([]);
  const [steps, setSteps] = useState<InvestigationStep[]>([]);
  const [shownSteps, setShownSteps] = useState(0);
  const [investigating, setInvestigating] = useState(false);
  const [suggestions, setSuggestions] = useState<AgentSuggestion[]>([]);
  const [inferredLinks, setInferredLinks] = useState<InferredLink[]>([]);
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  const [precedents, setPrecedents] = useState<Precedent[]>([]);
  const [precedentsOpen, setPrecedentsOpen] = useState(false);
  const [openSuggestionId, setOpenSuggestionId] = useState<string | null>(null);
  const [streamNodeId, setStreamNodeId] = useState<string | null>(null);
  const [narrating, setNarrating] = useState(false);
  const reviewToken = useRef(0);
  const [deepLinkReady, setDeepLinkReady] = useState(false);
  const [actorsReady, setActorsReady] = useState(false);
  const [caseGraphLoaded, setCaseGraphLoaded] = useState(false);
  const requestedNodeId = useRef<string | null>(null);

  // Apply URL context before fetching graph data so actor and organization stay aligned.
  useEffect(() => {
    const deepLink = parseGraphDeepLink(window.location.search);
    requestedNodeId.current = deepLink.selected;
    if (deepLink.focus) setFocus(deepLink.focus);
    if (deepLink.actorId) setActorId(deepLink.actorId);
    if (deepLink.asOf) setAsOf(deepLink.asOf);
    if (deepLink.mode) setMode(deepLink.mode);
    if (deepLink.viewMode) setViewMode(deepLink.viewMode);
    setDeepLinkReady(true);
  }, []);

  // 1. Fetch available actors
  useEffect(() => {
    api<ActorRow[]>("/api/v1/actors")
      .then((people) => {
        setAllActors(people);
        const requestedActor = new URLSearchParams(window.location.search).get("actor_id");
        if (requestedActor && people.some((person) => person.id === requestedActor)) {
          setActorId(requestedActor);
        } else if (!people.some((p) => p.id === actorId) && people.length > 0) {
          const defaultActor = people.find((p) => p.id === "actor-maya") || people[0];
          setActorId(defaultActor.id);
        }
      })
      .catch(() => {})
      .finally(() => setActorsReady(true));
  }, []);

  const currentActor = allActors.find((a) => a.id === actorId);

  // Combined graph: Acme and Decagon together, without choosing a person.
  useEffect(() => {
    if (!deepLinkReady) return;
    const params = new URLSearchParams({
      scope: "all",
      as_of: asOf,
      mode,
    });
    api<CompanyGraph>(`/api/v1/company-graph?${params}`)
      .then((next) => {
        setGraph(next);
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  }, [deepLinkReady, asOf, mode, job?.status, pulse]);

  // 3. When focused on a specific case, fetch its snapshot
  useEffect(() => {
    if (!deepLinkReady || !actorsReady) return;
    if (!focus || !actorId) {
      setCaseNodes([]);
      setCaseEdges([]);
      setCaseGraphLoaded(false);
      return;
    }
    setCaseGraphLoaded(false);
    const view = currentActor?.role === "auditor" ? "auditor" : "agent";
    api<{ id: string; manifest_hash: string }>("/api/v1/snapshots", {
      method: "POST",
      body: JSON.stringify({
        case_id: focus,
        actor_id: actorId,
        as_of: asOf,
        mode,
      }),
    })
      .then(async (snapshot) => {
        const snapGraph = await api<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
          `/api/v1/snapshots/${snapshot.id}/graph?view=${view}`
        );

        setCaseNodes(snapGraph.nodes);
        setCaseEdges(snapGraph.edges);
        setCaseGraphLoaded(true);
      })
      .catch(() => {
        setCaseNodes([]);
        setCaseEdges([]);
        setCaseGraphLoaded(true);
      });
  }, [deepLinkReady, actorsReady, focus, actorId, asOf, mode]);

  // Select a deep-linked node only after confirming it is visible in the selected graph.
  useEffect(() => {
    const nodeId = requestedNodeId.current;
    if (!nodeId || !deepLinkReady || !actorsReady) return;
    if (focus) {
      if (!caseGraphLoaded) return;
      requestedNodeId.current = null;
      if (caseNodes.some((node) => node.id === nodeId)) {
        setSelected(nodeId);
        setInspectorOpen(true);
      }
      return;
    }
    if (!graph) return;
    requestedNodeId.current = null;
    if (graph.nodes.some((node) => node.id === nodeId)) {
      setSelected(nodeId);
      setInspectorOpen(true);
    }
  }, [deepLinkReady, actorsReady, focus, caseGraphLoaded, caseNodes, graph]);

  const activeNodes = useMemo(() => {
    if (focus && caseNodes.length > 0) return caseNodes;
    if (focus && graph?.nodes) return graph.nodes.filter((n) => n.group === focus || n.case_id === focus);
    return graph?.nodes || [];
  }, [focus, caseNodes, graph?.nodes]);

  const activeEdges = useMemo(() => {
    if (focus && caseEdges.length > 0) return caseEdges;
    if (focus && graph?.edges) {
      const nodeIds = new Set(activeNodes.map((n) => n.id));
      return graph.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
    }
    return graph?.edges || [];
  }, [focus, caseEdges, activeNodes, graph?.edges]);

  const currentNode =
    activeNodes.find((item) => item.id === selected) ||
    graph?.nodes.find((item) => item.id === selected) ||
    null;
  const inspectorNodes =
    currentNode && !activeNodes.some((item) => item.id === currentNode.id) ? graph?.nodes || activeNodes : activeNodes;
  const inspectorEdges = inspectorNodes === activeNodes ? activeEdges : graph?.edges || activeEdges;
  const nodeSummary = useNodeSummary(currentNode?.id || null, actorId, asOf, mode, "all");
  const focusedCase = (graph?.cases || []).find((item) => item.id === focus) || null;
  const links = currentNode ? linkedRecords(currentNode, inspectorNodes, inspectorEdges) : [];
  const gap = currentNode ? amountGap(currentNode.amount, links.filter((item) => item.edge.type === "CONTRADICTS").map((item) => item.node?.amount)) : null;
  const timeline = currentNode ? datedRecords(currentNode, links.flatMap((item) => (item.node ? [item.node] : []))) : [];

  function handleSelectNode(nodeId: string) {
    if (!nodeId) {
      setSelected(null);
      return;
    }
    const inView = activeNodes.some((item) => item.id === nodeId);
    const record = inView
      ? activeNodes.find((item) => item.id === nodeId) || null
      : graph?.nodes.find((item) => item.id === nodeId) || null;
    if (!record) return;
    if (!inView && focus) setFocus(null);
    setSelected(nodeId);
    setInspectorOpen(true);
  }

  function openSummaryNode(nodeId: string) {
    const node = graph?.nodes.find((item) => item.id === nodeId);
    if (focus && node && node.case_id !== focus && node.group !== focus) setFocus(null);
    handleSelectNode(nodeId);
  }

  useEffect(() => {
    const stored = loadPrecedents();
    setPrecedents(stored);
  }, []);

  useEffect(() => {
    if (precedents.length === 0) return;
    savePrecedents(precedents);
  }, [precedents]);

  useEffect(() => {
    reviewToken.current += 1;
    setSuggestions([]);
    setInferredLinks([]);
    setDismissedIds([]);
    setSteps([]);
    setShownSteps(0);
    setInvestigating(false);
    setNarrating(false);
    setOpenSuggestionId(null);
    setStreamNodeId(null);
    setCustomRootId(null);
  }, [actorId, asOf, mode]);

  useEffect(() => {
    if (!investigating) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || shownSteps >= steps.length) {
      setShownSteps(steps.length);
      setInvestigating(false);
      return;
    }
    const timer = window.setTimeout(() => setShownSteps((count) => count + 1), 480);
    return () => window.clearTimeout(timer);
  }, [investigating, shownSteps, steps.length]);

  const handleDrawnIds = useCallback((ids: string[]) => {
    setDrawnIds((prev) => (prev.length === ids.length && prev.every((id, index) => id === ids[index]) ? prev : ids));
  }, []);

  async function askAgent() {
    if (!currentNode || !graph || investigating || narrating) return;
    if (suggestions.some((item) => item.id === currentNode.id) || inferredLinks.some((link) => link.target === currentNode.id)) return;
    const token = reviewToken.current + 1;
    reviewToken.current = token;
    const nodeId = currentNode.id;
    const result = buildInvestigation({
      node: currentNode,
      nodes: graph.nodes,
      edges: graph.edges,
      cases: graph.cases,
      drawnIds,
      precedents,
      blockedIds: [...dismissedIds, ...inferredLinks.map((link) => link.target)],
    });
    const thinking = result.steps[0]?.text || "The visible records were read.";
    const action = result.steps[1]?.text || "The visible records were compared.";
    const lines = result.steps[1]?.lines || [];
    const verdict = result.steps[2]?.text || "The comparison is limited to records already visible.";
    setStreamNodeId(nodeId);
    setSteps([]);
    setShownSteps(0);
    setInvestigating(false);
    setNarrating(true);
    setSuggestions(result.suggestions);
    setOpenSuggestionId(null);
    setInspectorOpen(true);
    const started = Date.now();
    let nextSteps = result.steps;
    try {
      const view = currentActor?.role === "auditor" ? "auditor" : "agent";
      const params = new URLSearchParams({
        organization_id: "all",
        actor_id: actorId,
        as_of: asOf,
        mode,
        view,
        node_id: nodeId,
      });
      const narrated = await api<{ narrated: boolean; steps: InvestigationStep[] }>(`/api/v1/company-graph/investigate?${params}`, {
        method: "POST",
        body: JSON.stringify({ thinking, action, lines, verdict }),
      });
      if (narrated.steps?.length === 3 && narrated.steps.every((step) => step.text)) {
        nextSteps = narrated.steps.map((step, index) => ({
          kind: step.kind,
          text: step.text,
          lines: index === 1 ? lines : step.lines,
        }));
      }
    } catch {
      nextSteps = result.steps;
    }
    const wait = Math.max(0, 800 - (Date.now() - started));
    await new Promise((resolve) => window.setTimeout(resolve, wait));
    if (reviewToken.current !== token) return;
    setSteps(nextSteps);
    setShownSteps(nextSteps.length > 0 ? 1 : 0);
    setNarrating(false);
    setInvestigating(nextSteps.length > 1);
  }

  function approveSuggestion(id: string) {
    const suggestion = suggestions.find((item) => item.id === id);
    if (!suggestion) return;
    setInferredLinks((prev) =>
      prev.some((link) => link.target === id && link.source === suggestion.anchorId)
        ? prev
        : [...prev, { id: `agent:${suggestion.anchorId}:${id}`, source: suggestion.anchorId, target: id }]
    );
    setSuggestions((prev) => prev.filter((item) => item.id !== id));
    setOpenSuggestionId(null);
    const anchor = graph?.nodes.find((item) => item.id === suggestion.anchorId);
    setPrecedents((prev) => {
      const learned = learnPrecedent(suggestion, anchor?.label || suggestion.anchorId);
      const rest = prev.filter((item) => item.id !== learned.id);
      return [learned, ...rest];
    });
  }

  function dismissSuggestion(id: string) {
    const suggestion = suggestions.find((item) => item.id === id);
    setSuggestions((prev) => prev.filter((item) => item.id !== id));
    setOpenSuggestionId(null);
    if (suggestion) setDismissedIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }

  function togglePrecedent(id: string) {
    setPrecedents((prev) => prev.map((item) => (item.id === id ? { ...item, kept: !item.kept } : item)));
  }

  const openSuggestion = suggestions.find((item) => item.id === openSuggestionId) || null;
  const streamSteps = streamNodeId && currentNode?.id === streamNodeId ? steps : [];
  const streamShown = streamNodeId && currentNode?.id === streamNodeId ? shownSteps : 0;
  const heldByAgent = Boolean(
    currentNode &&
      (suggestions.some((item) => item.id === currentNode.id) || inferredLinks.some((link) => link.target === currentNode.id))
  );
  const reviewThinking = narrating && streamNodeId === currentNode?.id;

  return (
    <main className="fullscreen-workspace">
      {/* 1. Fullscreen Canvas Graph (Interactive Flow or Atom Network) */}
      <section className="fullscreen-stage">
        <EvidenceGraph
          nodes={activeNodes}
          edges={activeEdges}
          selected={selected}
          focused={focus}
          viewMode={viewMode}
          rootId={customRootId}
          onRootChange={setCustomRootId}
          onSelect={handleSelectNode}
          suggestions={suggestions}
          inferredLinks={inferredLinks}
          openSuggestionId={openSuggestionId}
          onOpenSuggestion={(id) => {
            setOpenSuggestionId(id);
            setInspectorOpen(true);
          }}
          onApproveSuggestion={approveSuggestion}
          onDismissSuggestion={dismissSuggestion}
          onDrawnIds={handleDrawnIds}
        />
      </section>

      {/* 2. Minimalist Floating Header Bar */}
      <header className="floating-topbar">
        {/* Left: Brand & Scope */}
        <div className="floating-glass">
          <a href="/" className="brand-title" style={{ padding: "0 4px", display: "inline-flex", alignItems: "center" }}>
            <img src="/comma-logo.png" alt="Comma" className="brand-logo-img" style={{ height: "30px", width: "auto" }} />
          </a>

          <div className="pill-select">
            <select
              aria-label="Scope or Case"
              value={focus || ""}
              onChange={(e) => {
                const val = e.target.value || null;
                setFocus(val);
                setCustomRootId(null);
                setSelected(null);
              }}
            >
              <option value="">All cases</option>
              {(graph?.cases || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} · {c.vendor}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Right: Controls & Inspector Toggle */}
        <div className="floating-glass">
          <div className="presets" role="group" aria-label="View mode">
            <button
              className={viewMode === "web" ? "primary" : ""}
              onClick={() => setViewMode("web")}
              title="Step-by-step interactive flow graph"
            >
              Flow
            </button>
            <button
              className={viewMode === "atom" ? "primary" : ""}
              onClick={() => setViewMode("atom")}
              title="3D rotating atom graph"
            >
              Atom
            </button>
          </div>

          <button
            className={inspectorOpen ? "primary" : ""}
            onClick={() => {
              setInspectorOpen((prev) => {
                if (prev) setSelected(null);
                return !prev;
              });
            }}
            title="Toggle Details Drawer"
          >
            {inspectorOpen ? "✕ Close" : "◨ Details"}
          </button>

          <button
            className="fab"
            aria-label="Add documents"
            title="Add document"
            onClick={() => setOpen(true)}
          >
            +
          </button>
        </div>
      </header>

      {/* 3. Floating Collapsible Inspector Sidebar */}
      <aside className={`floating-inspector${inspectorOpen ? "" : " collapsed"}`}>
        {currentNode ? (
          <>
            {/* Node Context & Header */}
            <div className="sidebar-header">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div className="cell-eyebrow">
                  {currentNode.type.replace(/_/g, " ")} · {currentNode.case_id || currentNode.group || "Company Evidence"}
                </div>
                <button
                  className="inspector-close-btn"
                  onClick={() => {
                    setInspectorOpen(false);
                    setSelected(null);
                  }}
                  title="Collapse inspector"
                >
                  ✕
                </button>
              </div>

              <div className="header-amount-row">
                <span className="amount">
                  {currentNode.redacted
                    ? "Withheld"
                    : currentNode.amount
                    ? `$${currentNode.amount}`
                    : "Reference"}
                </span>

                {currentNode.redacted ? (
                  <span className="status-badge" style={{ color: "#94A3B8" }}>
                    <span className="status-dot grey" />
                    Withheld
                  </span>
                ) : gap ? (
                  <span className="status-badge needs-review">
                    <span className="status-dot orange" />
                    Differs by ${gap}
                  </span>
                ) : links.some((item) => item.edge.type === "CONTRADICTS") ? (
                  <span className="status-badge needs-review">
                    <span className="status-dot orange" />
                    Amounts differ
                  </span>
                ) : links.some((item) => item.edge.type === "RECONCILES_WITH") ? (
                  <span className="status-badge verified">
                    <span className="status-dot green" />
                    Amounts match
                  </span>
                ) : currentNode.type === "DECISION" ? (
                  <span className="status-badge">
                    <span className="status-dot black" />
                    Recorded
                  </span>
                ) : (
                  <span className="status-badge" style={{ color: "var(--muted)" }}>
                    <span className="status-dot green" />
                    In snapshot
                  </span>
                )}
              </div>

              <div className="inspector-title">{currentNode.label}</div>
              {currentNode.document_id && currentNode.document_id !== currentNode.label ? (
                <div className="cell-eyebrow">{currentNode.document_id}</div>
              ) : null}

              {openSuggestion && openSuggestion.id === currentNode.id ? (
                <div className="suggestion-card">
                  <div className="suggestion-kicker">Suggested by Comma Agent</div>
                  <p>{`${openSuggestion.reason.charAt(0).toUpperCase()}${openSuggestion.reason.slice(1)}.`}</p>
                  <div className="suggestion-actions">
                    <button type="button" className="primary" onClick={() => approveSuggestion(openSuggestion.id)}>
                      Approve
                    </button>
                    <button type="button" onClick={() => dismissSuggestion(openSuggestion.id)}>
                      Dismiss
                    </button>
                  </div>
                </div>
              ) : null}

              <div className="header-summary">
                <NodeSummary
                  summary={nodeSummary.summary}
                  error={nodeSummary.error}
                  nodes={inspectorNodes}
                  onOpen={openSummaryNode}
                />
              </div>

              <div className="inspector-agent-row">
                {heldByAgent ? (
                  <p className="agent-held">Added by an investigation. It is not investigated again.</p>
                ) : (
                  <button type="button" className="primary ask-agent-btn" onClick={askAgent} disabled={reviewThinking || (investigating && streamNodeId === currentNode.id)}>
                    {reviewThinking ? "Thinking" : "Ask agent to investigate"}
                  </button>
                )}
                <button type="button" className={precedentsOpen ? "primary" : ""} aria-pressed={precedentsOpen} onClick={() => setPrecedentsOpen((openPanel) => !openPanel)}>
                  Learned precedents
                </button>
              </div>
              <InvestigationPanel
                steps={streamSteps}
                shown={streamShown}
                thinking={reviewThinking}
                precedentsOpen={precedentsOpen}
                precedents={precedents}
                suggestion={openSuggestion && openSuggestion.id !== currentNode.id ? openSuggestion : null}
                onApprove={approveSuggestion}
                onDismiss={dismissSuggestion}
                onTogglePrecedent={togglePrecedent}
              />

              {currentNode.formula ? (
                <div className="formula-box">
                  <span className="label">Formula</span>
                  <span className="code">{currentNode.formula}</span>
                </div>
              ) : null}

              <button
                className="set-root-action-btn"
                onClick={() => setCustomRootId(currentNode.id)}
                title="Anchor this record as the root of the flow"
              >
                ✦ Set as Flow Root
              </button>
            </div>

            {/* Evidence Document Metadata */}
            <div className="evidence-card">
              <div className="trace-title" style={{ marginBottom: "0.4rem" }}>
                Document Details
              </div>
              <table className="meta-table">
                <tbody>
                  {currentNode.document_id ? (
                    <tr>
                      <td className="key">Id</td>
                      <td className="val">{currentNode.document_id}</td>
                    </tr>
                  ) : null}
                  <tr>
                    <td className="key">Type</td>
                    <td className="val" style={{ textTransform: "capitalize" }}>
                      {currentNode.type.toLowerCase().replace(/_/g, " ")}
                    </td>
                  </tr>
                  <tr>
                    <td className="key">Knowable</td>
                    <td className="val">{currentNode.available_from ? currentNode.available_from.slice(0, 10) : "—"}</td>
                  </tr>
                  <tr>
                    <td className="key">Document date</td>
                    <td className="val">{currentNode.document_date ? currentNode.document_date.slice(0, 10) : "—"}</td>
                  </tr>
                  <tr>
                    <td className="key">Origin</td>
                    <td className="val">{currentNode.origin || "—"}</td>
                  </tr>
                </tbody>
              </table>
              {currentNode.version_id && !currentNode.redacted ? (
                <button className="preview-open" onClick={() => setPreviewId(currentNode.version_id)}>
                  Preview document
                </button>
              ) : null}
            </div>

            {/* Upstream / Downstream Graph Relationships */}
            <div>
              <div className="trace-title">Connected ({links.length})</div>
              <div className="link-list">
                {links.length === 0 ? (
                  <div className="empty-rel-box">
                    <span className="muted">No other record in this snapshot links here.</span>
                  </div>
                ) : (
                  links.map(({ edge, node: otherNode }) => {
                    const otherId = edge.source === currentNode.id ? edge.target : edge.source;
                    const otherLabel = otherNode?.label || otherId;
                    return (
                      <button
                        key={edge.id}
                        className="rel-card-btn"
                        onClick={() => handleSelectNode(otherId)}
                        title={`Inspect ${otherLabel}`}
                      >
                        <div className="rel-card-header">
                          <span className={`rel-badge ${edge.type.toLowerCase()}`}>
                            {edge.type.replace(/_/g, " ")}
                          </span>
                          <span className="rel-other-label">{otherLabel}</span>
                          {otherNode?.amount && !otherNode.redacted ? <span className="rel-amt">${otherNode.amount}</span> : null}
                        </div>
                        {edge.explanation ? <div className="rel-expl">{edge.explanation}</div> : null}
                      </button>
                    );
                  })
                )}
              </div>
            </div>

            {/* Timeline Context */}
            <div className="trace-section">
              <div className="trace-title">Timeline Context</div>
              <div className="trace-list">
                {timeline.length === 0 ? (
                  <div className="empty-rel-box">
                    <span className="muted">No date is stored on this record.</span>
                  </div>
                ) : (
                  timeline.map((item) => {
                    const when = item.available_from || item.document_date || "";
                    return (
                      <div className="trace-item" key={item.id}>
                        <span className="trace-date">{shortDate(when)}</span>
                        <span className="trace-marker"><span className={`trace-dot ${item.redacted ? "grey" : item.id === currentNode.id ? "black" : "green"}`} /></span>
                        <span className="trace-desc">{item.label}</span>
                        <span className="trace-amt">{item.redacted ? "—" : item.amount ? `$${item.amount}` : "—"}</span>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </>
        ) : (
          /* Empty / Default Overview State */
          <div>
            <div className="sidebar-header">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div className="cell-eyebrow">
                  {focusedCase ? `${focusedCase.id} · ${focusedCase.vendor}` : "All cases"}
                </div>
                <button
                  className="inspector-close-btn"
                  onClick={() => setInspectorOpen(false)}
                  title="Collapse inspector"
                >
                  ✕
                </button>
              </div>

              <div className="header-amount-row">
                <span className="amount">{graph?.corpus ? `${graph.corpus.visible} documents` : "—"}</span>
                <span className="status-badge verified">
                  <span className="status-dot green" />
                  {graph?.corpus ? `${graph.corpus.visible} visible` : "In snapshot"}
                </span>
              </div>

              <p className="header-summary">
                {focusedCase?.finding || `${graph?.cases.length || 0} cases in this snapshot.`}
              </p>
            </div>
          </div>
        )}

        {/* Document Search */}
        <div style={{ marginTop: "0.2rem" }}>
          <input
            className="search-box"
            placeholder="Search documents, invoices, emails…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          {query && (
            <div className="doc-results">
              {filteredDocuments(graph, query).map((doc) => (
                <button
                  key={doc.version_id}
                  className="doc-result-btn"
                  onClick={() => {
                    const match =
                      activeNodes.find((n) => n.version_id === doc.version_id || n.document_id === doc.id || n.label === doc.id) ||
                      graph?.nodes.find((n) => n.version_id === doc.version_id || n.document_id === doc.id || n.label === doc.id);
                    if (match) handleSelectNode(match.id);
                    setPreviewId(doc.version_id);
                  }}
                >
                  <strong>{doc.title || doc.id}</strong>
                  <span>{[doc.title && doc.title !== doc.id ? doc.id : "", doc.amount ? `$${doc.amount}` : doc.kind].filter(Boolean).join(" · ")}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Quick Links */}
        {focus && (
          <div className="row" style={{ marginTop: "0.2rem", fontSize: "0.8rem" }}>
            <a href={`/cases/${focus}/replay`} className="nav-link" style={{ padding: "0.2rem 0.4rem" }}>
              Run replay →
            </a>
            <a href={`/cases/${focus}/compare`} className="nav-link" style={{ padding: "0.2rem 0.4rem" }}>
              Before/after →
            </a>
          </div>
        )}
      </aside>

      <IngestModal
        open={open}
        job={job}
        onClose={() => setOpen(false)}
        onStarted={(next) => {
          setJob(next);
          setOpen(true);
        }}
      />
      {previewId ? (
        <DocumentPreview
          versionId={previewId}
          actorId={actorId}
          asOf={asOf}
          mode={mode}
          organizationId="all"
          onClose={() => setPreviewId(null)}
        />
      ) : null}
    </main>
  );
}

function filteredDocuments(graph: CompanyGraph | null, query: string) {
  const needle = query.trim().toLowerCase();
  return (graph?.documents || [])
    .filter(
      (document) =>
        !needle ||
        `${document.id} ${document.kind} ${document.title} ${document.case_id}`
          .toLowerCase()
          .includes(needle)
    )
    .slice(0, 15);
}

function linkedRecords(node: GraphNode, nodes: GraphNode[], edges: GraphEdge[]) {
  return edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .map((edge) => {
      const otherId = edge.source === node.id ? edge.target : edge.source;
      return { edge, node: nodes.find((item) => item.id === otherId) || null };
    });
}

function amountGap(amount: string | null, others: (string | null | undefined)[]) {
  const left = Number(amount);
  const values = others.map((item) => Number(item)).filter((item) => Number.isFinite(item) && item !== left);
  if (!Number.isFinite(left) || values.length === 0) return null;
  return Math.abs(left - values[0]).toFixed(2);
}

function datedRecords(node: GraphNode, neighbors: GraphNode[]) {
  const seen = new Set<string>();
  return [node, ...neighbors]
    .filter((item) => {
      if (seen.has(item.id) || !(item.available_from || item.document_date)) return false;
      seen.add(item.id);
      return true;
    })
    .sort((a, b) => (a.available_from || a.document_date || "").localeCompare(b.available_from || b.document_date || ""));
}

function shortDate(iso: string) {
  const month = Number(iso.slice(5, 7));
  const day = Number(iso.slice(8, 10));
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  if (!day || month < 1 || month > 12) return iso.slice(0, 10);
  return `${names[month - 1]} ${day}`;
}
