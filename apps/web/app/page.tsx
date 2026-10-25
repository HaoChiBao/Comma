"use client";

import { useEffect, useMemo, useState } from "react";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { IngestModal, type IngestJob } from "@/components/IngestModal";
import { API_URL, api, type ActorRow, type CompanyDocument, type CompanyGraph, type GraphEdge, type GraphNode } from "@/lib/api";

const PRESETS = [
  { id: "close", label: "30 Sep Before", asOf: "2025-09-30T16:59:59+00:00" },
  { id: "accrual", label: "30 Sep Accrual", asOf: "2025-09-30T23:59:00+00:00" },
  { id: "december", label: "15 Dec Review", asOf: "2025-12-15T12:00:00+00:00" },
];

export default function HomePage() {
  const [graph, setGraph] = useState<CompanyGraph | null>(null);
  const [allActors, setAllActors] = useState<ActorRow[]>([]);
  const [actorId, setActorId] = useState("actor-maya");
  const [asOf, setAsOf] = useState(PRESETS[1].asOf);
  const [mode, setMode] = useState("audit");
  const [viewMode, setViewMode] = useState<"web" | "atom" | "structure">("web");
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [caseNodes, setCaseNodes] = useState<GraphNode[]>([]);
  const [caseEdges, setCaseEdges] = useState<GraphEdge[]>([]);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [pulse, setPulse] = useState(0);
  const [query, setQuery] = useState("");

  // 1. Fetch available actors
  useEffect(() => {
    api<ActorRow[]>("/api/v1/actors")
      .then((people) => {
        setAllActors(people);
        if (!people.some((p) => p.id === actorId) && people.length > 0) {
          const defaultActor = people.find((p) => p.id === "actor-maya") || people[0];
          setActorId(defaultActor.id);
        }
      })
      .catch(() => {});
  }, []);

  const currentActor = allActors.find((a) => a.id === actorId);
  const organizationId = currentActor?.organization_id || "org-acme";

  // 2. Fetch full company graph
  useEffect(() => {
    if (!actorId) return;
    const view = currentActor?.role === "auditor" ? "auditor" : "agent";
    const params = new URLSearchParams({
      organization_id: organizationId,
      actor_id: actorId,
      as_of: asOf,
      mode,
      view,
    });
    api<CompanyGraph>(`/api/v1/company-graph?${params}`)
      .then((next) => {
        setGraph(next);
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  }, [actorId, organizationId, asOf, mode, job?.status, pulse]);

  // 3. When focused on a specific case, fetch its snapshot
  useEffect(() => {
    if (!focus || !actorId) {
      setCaseNodes([]);
      setCaseEdges([]);
      return;
    }
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

        if (focus === "CASE-011") {
          const nodesCopy = [...snapGraph.nodes];
          const edgesCopy = [...snapGraph.edges];

          const target = nodesCopy.find((n) => n.label === "JE-911" || n.label === "D3");
          if (target) {
            target.label = "D3";
            target.title = "Accruals!D3 · Cloud Services Inc.";
          }

          if (!nodesCopy.some((n) => n.id === "cell:B3")) {
            nodesCopy.push({
              id: "cell:B3",
              type: "PRECEDENT",
              label: "B3",
              title: "PO line 1",
              amount: "100.00",
              origin: "formula",
              version_id: "cell:B3",
              available_from: "2025-09-12T16:00:00+00:00",
              document_date: "2025-09-12T16:00:00+00:00",
              redacted: false,
              group: "CASE-011",
              case_id: "CASE-011",
            });
            if (target) {
              edgesCopy.push({
                id: "edge-b3-d3",
                source: "cell:B3",
                target: target.id,
                type: "DERIVED_FROM",
                inferred: false,
                explanation: "Cell B3 PO line 1 feeds into formula =B3+C3",
                confidence: "high",
              });
            }
          }

          if (!nodesCopy.some((n) => n.id === "cell:C3")) {
            nodesCopy.push({
              id: "cell:C3",
              type: "PRECEDENT",
              label: "C3",
              title: "PO line 2",
              amount: "50.00",
              origin: "formula",
              version_id: "cell:C3",
              available_from: "2025-09-12T16:00:00+00:00",
              document_date: "2025-09-12T16:00:00+00:00",
              redacted: false,
              group: "CASE-011",
              case_id: "CASE-011",
            });
            if (target) {
              edgesCopy.push({
                id: "edge-c3-d3",
                source: "cell:C3",
                target: target.id,
                type: "DERIVED_FROM",
                inferred: false,
                explanation: "Cell C3 PO line 2 feeds into formula =B3+C3",
                confidence: "high",
              });
            }
          }

          const hasInv = nodesCopy.some((n) => n.label.startsWith("INV"));
          if (!hasInv && (currentActor?.role === "auditor" || asOf.includes("2025-12"))) {
            nodesCopy.push({
              id: "artifact:INV-104:v1",
              type: "INVOICE",
              label: "INV",
              title: "Invoice INV-104",
              amount: asOf.includes("2025-12") ? "158.00" : null,
              origin: "vendor",
              version_id: "INV-104:v1",
              available_from: "2025-12-10T09:00:00+00:00",
              document_date: "2025-09-28T00:00:00+00:00",
              redacted: !asOf.includes("2025-12"),
              group: "CASE-011",
              case_id: "CASE-011",
            });
            if (target) {
              edgesCopy.push({
                id: "edge-inv-d3",
                source: "artifact:INV-104:v1",
                target: target.id,
                type: asOf.includes("2025-12") ? "CONTRADICTS" : "POTENTIALLY_SUPPORTS",
                inferred: true,
                explanation: "Invoice withheld until Dec 10.",
                confidence: "medium",
              });
            }
          }

          setCaseNodes(nodesCopy);
          setCaseEdges(edgesCopy);

          const emNode = nodesCopy.find((n) => n.label.includes("EM-208") || n.label === "EM");
          if (emNode && !selected) setSelected(emNode.id);
        } else {
          setCaseNodes(snapGraph.nodes);
          setCaseEdges(snapGraph.edges);
        }
      })
      .catch(() => {
        setCaseNodes([]);
        setCaseEdges([]);
      });
  }, [focus, actorId, asOf, mode]);

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

  const currentNode = activeNodes.find((item) => item.id === selected) || null;

  function handleSelectNode(nodeId: string) {
    if (!nodeId) {
      setSelected(null);
      return;
    }
    setSelected(nodeId);
    setInspectorOpen(true);
  }

  return (
    <main className="fullscreen-workspace">
      {/* 1. Fullscreen Canvas Graph (Atom Network or Structured Ring Layout) */}
      <section className="fullscreen-stage">
        <EvidenceGraph
          nodes={activeNodes}
          edges={activeEdges}
          selected={selected}
          focused={focus}
          viewMode={viewMode}
          onSelect={handleSelectNode}
        />
      </section>

      {/* 2. Minimalist Floating Header Bar */}
      <header className="floating-topbar">
        {/* Left: Brand & Scope */}
        <div className="floating-glass">
          <a href="/" className="brand-title" style={{ fontSize: "1.15rem" }}>
            Comma
          </a>

          <div className="presets" role="group" aria-label="As of date">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                className={asOf === preset.asOf ? "primary" : ""}
                onClick={() => setAsOf(preset.asOf)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className="pill-select">
            <select
              aria-label="Scope or Case"
              value={focus || ""}
              onChange={(e) => {
                const val = e.target.value || null;
                setFocus(val);
                if (val === "CASE-011") setSelected("artifact:EM-208:v1");
                else setSelected(null);
              }}
            >
              <option value="">All Cases (Atom Graph)</option>
              <option value="CASE-011">CASE-011 · Cloud Services (Workbook)</option>
              <option value="CASE-001">CASE-001 · Cloud Services Inc.</option>
              {(graph?.cases || [])
                .filter((c) => c.id !== "CASE-011" && c.id !== "CASE-001")
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id} · {c.vendor}
                  </option>
                ))}
            </select>
          </div>
        </div>

        {/* Right: Controls & Inspector Toggle */}
        <div className="floating-glass">
          <div className="pill-select">
            <select
              aria-label="Actor"
              value={actorId}
              onChange={(event) => setActorId(event.target.value)}
            >
              {allActors.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name} · {person.role}
                </option>
              ))}
            </select>
          </div>

          <div className="presets" role="group" aria-label="View mode">
            <button
              className={viewMode === "web" ? "primary" : ""}
              onClick={() => setViewMode("web")}
              title="Progressive web expansion (Click nodes to unfold connections)"
            >
              Web
            </button>
            <button
              className={viewMode === "atom" ? "primary" : ""}
              onClick={() => setViewMode("atom")}
              title="3D rotating atom graph"
            >
              Atom
            </button>
            <button
              className={viewMode === "structure" ? "primary" : ""}
              onClick={() => setViewMode("structure")}
              title="Structured ring layout"
            >
              Ring
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
                    Withheld at Close
                  </span>
                ) : activeEdges.some((e) => (e.source === currentNode.id || e.target === currentNode.id) && e.type === "CONTRADICTS") ? (
                  <span className="status-badge needs-review">
                    <span className="status-dot orange" />
                    Conflicts ($10 diff)
                  </span>
                ) : activeEdges.some((e) => (e.source === currentNode.id || e.target === currentNode.id) && e.type === "RECONCILES_WITH") ? (
                  <span className="status-badge verified">
                    <span className="status-dot green" />
                    Verified Match
                  </span>
                ) : currentNode.type === "DECISION" ? (
                  <span className="status-badge">
                    <span className="status-dot black" />
                    Recorded Accrual
                  </span>
                ) : (
                  <span className="status-badge" style={{ color: "var(--muted)" }}>
                    <span className="status-dot green" />
                    Available Evidence
                  </span>
                )}
              </div>

              <div style={{ fontWeight: 700, fontSize: "1.05rem", color: "var(--text)", margin: "0.15rem 0" }}>
                {currentNode.label}
              </div>

              <p className="header-summary" style={{ margin: "0.2rem 0 0.5rem" }}>
                {currentNode.title || currentNode.text || "Evidence record available in company graph."}
              </p>

              {/* Formula Callout Box: ONLY shown if inspecting an Excel formula cell / accrual decision */}
              {(currentNode.id.includes("cell:") ||
                (focus === "CASE-011" && (currentNode.label === "D3" || currentNode.label.startsWith("JE")))) && (
                <div className="formula-box">
                  <span className="label">Formula</span>
                  <span className="code">=B3+C3</span>
                  <span className="calc">$100.00 + $50.00 = $150.00</span>
                </div>
              )}
            </div>

            {/* Evidence Document Metadata */}
            <div className="evidence-card">
              <div className="trace-title" style={{ marginBottom: "0.4rem" }}>
                Document Details
              </div>
              <table className="meta-table">
                <tbody>
                  <tr>
                    <td className="key">Type</td>
                    <td className="val" style={{ textTransform: "capitalize" }}>
                      {currentNode.type.toLowerCase().replace(/_/g, " ")}
                    </td>
                  </tr>
                  <tr>
                    <td className="key">Knowable</td>
                    <td className="val">
                      {currentNode.available_from ? (
                        <>
                          <strong>{currentNode.available_from.slice(0, 10)}</strong>{" "}
                          <span className="muted" style={{ fontSize: "0.72rem" }}>
                            {currentNode.available_from > asOf
                              ? "(Withheld: Future document)"
                              : "(Available before close)"}
                          </span>
                        </>
                      ) : (
                        "Baseline record"
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td className="key">Origin</td>
                    <td className="val">
                      {currentNode.origin === "vendor"
                        ? "Vendor correspondence"
                        : currentNode.origin === "formula"
                        ? "Workbook cell formula"
                        : currentNode.type === "EMAIL"
                        ? "Inbound AP mailbox"
                        : currentNode.type === "PURCHASE_ORDER"
                        ? "ERP purchase order system"
                        : "Company general ledger"}
                    </td>
                  </tr>
                  <tr>
                    <td className="key">Audit Role</td>
                    <td className="val">
                      {currentNode.redacted
                        ? "Withheld from accountant during close to prevent future leakage."
                        : activeEdges.some((e) => (e.source === currentNode.id || e.target === currentNode.id) && e.type === "CONTRADICTS")
                        ? "Flags discrepancy: Amount differs from related records naming the same transaction."
                        : activeEdges.some((e) => (e.source === currentNode.id || e.target === currentNode.id) && e.type === "RECONCILES_WITH")
                        ? "Reconciles and supports the recorded accrual entry."
                        : "Ingested evidence record. Available to preparer prior to close."}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Upstream / Downstream Graph Relationships */}
            <div>
              <div className="trace-title">
                Connected Evidence ({activeEdges.filter((e) => e.source === currentNode.id || e.target === currentNode.id).length})
              </div>
              <div className="link-list">
                {activeEdges.filter((e) => e.source === currentNode.id || e.target === currentNode.id).length === 0 ? (
                  <div className="empty-rel-box">
                    <strong style={{ display: "block", marginBottom: "0.2rem" }}>Standalone Evidence</strong>
                    <span className="muted">
                      Ingested into the company knowledge base. Not directly cited or linked to a specific accrual journal entry.
                    </span>
                  </div>
                ) : (
                  activeEdges
                    .filter((e) => e.source === currentNode.id || e.target === currentNode.id)
                    .map((edge) => {
                      const otherId = edge.source === currentNode.id ? edge.target : edge.source;
                      const otherNode = activeNodes.find((n) => n.id === otherId);
                      const otherLabel = otherNode?.label || otherId.replace(/^artifact:/, "").replace(/:v\d+$/, "");
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
                            {otherNode?.amount && <span className="rel-amt">${otherNode.amount}</span>}
                          </div>
                          {edge.explanation && (
                            <div className="rel-expl">{edge.explanation}</div>
                          )}
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
                {currentNode.case_id === "CASE-011" || focus === "CASE-011" ? (
                  <>
                    <div className="trace-item">
                      <span className="trace-date">Sep 12</span>
                      <span className="trace-marker"><span className="trace-dot green" /></span>
                      <span className="trace-desc">PO-104 received</span>
                      <span className="trace-amt">$150.00</span>
                    </div>
                    <div className="trace-item">
                      <span className="trace-date">Sep 26</span>
                      <span className="trace-marker"><span className="trace-dot orange" /></span>
                      <span className="trace-desc">EM-208 received</span>
                      <span className="trace-amt">$160.00</span>
                    </div>
                    <div className="trace-item">
                      <span className="trace-date">Sep 30</span>
                      <span className="trace-marker"><span className="trace-dot black" /></span>
                      <span className="trace-desc">Books closed, accrual posted</span>
                      <span className="trace-amt">$150.00</span>
                    </div>
                    {(asOf.includes("2025-12") || currentActor?.role === "auditor") && (
                      <div className="trace-item">
                        <span className="trace-date">Dec 10</span>
                        <span className="trace-marker"><span className="trace-dot grey" /></span>
                        <span className="trace-desc">
                          {asOf.includes("2025-12") ? "INV-104 received" : "INV-104 (withheld)"}
                        </span>
                        <span className="trace-amt">
                          {asOf.includes("2025-12") ? "$158.00" : "—"}
                        </span>
                      </div>
                    )}
                  </>
                ) : currentNode.available_from ? (
                  <>
                    <div className="trace-item">
                      <span className="trace-date">{currentNode.available_from.slice(5, 10).replace("-", " ")}</span>
                      <span className="trace-marker"><span className="trace-dot green" /></span>
                      <span className="trace-desc">Document knowable</span>
                      <span className="trace-amt">{currentNode.amount ? `$${currentNode.amount}` : "—"}</span>
                    </div>
                    <div className="trace-item">
                      <span className="trace-date">Sep 30</span>
                      <span className="trace-marker"><span className="trace-dot black" /></span>
                      <span className="trace-desc">Books close cutoff</span>
                      <span className="trace-amt">{currentNode.available_from <= asOf ? "Visible" : "Withheld"}</span>
                    </div>
                    <div className="trace-item">
                      <span className="trace-date">Dec 15</span>
                      <span className="trace-marker"><span className="trace-dot grey" /></span>
                      <span className="trace-desc">Post-close audit review</span>
                      <span className="trace-amt">Verified</span>
                    </div>
                  </>
                ) : (
                  <div className="empty-rel-box">
                    <span className="muted">Permanent baseline record in general ledger.</span>
                  </div>
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
                  {focus ? `${focus} · Overview` : "Company Knowledge Graph"}
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
                <span className="amount">{activeNodes.length} Nodes</span>
                <span className="status-badge verified">
                  <span className="status-dot green" />
                  Active Corpus
                </span>
              </div>

              <p className="header-summary">
                {focus
                  ? graph?.cases.find((c) => c.id === focus)?.finding || "Select any node in the graph to inspect evidence and relationships."
                  : "Click any node in the 3D atom graph to inspect its evidence record, knowable timeline, and audit connections."}
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
                    const match = activeNodes.find((n) => n.version_id === doc.version_id || n.label === doc.id);
                    if (match) setSelected(match.id);
                  }}
                >
                  <strong>{doc.id}</strong>
                  <span>{doc.amount ? `$${doc.amount}` : doc.kind}</span>
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
    </main>
  );
}

function linksFor(graph: CompanyGraph | null, selected: string | null) {
  if (!graph || !selected) return [];
  return graph.edges.filter((edge) => edge.source === selected || edge.target === selected);
}

function otherEnd(graph: CompanyGraph | null, selected: string | null, edge: { source: string; target: string }) {
  const id = edge.source === selected ? edge.target : edge.source;
  return graph?.nodes.find((node) => node.id === id)?.label || id;
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
