"use client";

import { useEffect, useState } from "react";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { IngestModal, type IngestJob } from "@/components/IngestModal";
import { API_URL, api, type CompanyDocument, type CompanyGraph } from "@/lib/api";

const PRESETS = [
  { id: "close", label: "30 Sep, before close", asOf: "2025-09-30T16:59:59+00:00" },
  { id: "accrual", label: "30 Sep, after accrual", asOf: "2025-09-30T23:59:00+00:00" },
  { id: "december", label: "15 Dec review", asOf: "2025-12-15T12:00:00+00:00" },
];

export default function HomePage() {
  const [graph, setGraph] = useState<CompanyGraph | null>(null);
  const [actorId, setActorId] = useState("actor-decagon-finance");
  const [asOf, setAsOf] = useState(PRESETS[2].asOf);
  const [mode, setMode] = useState("audit");
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [pulse, setPulse] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const view = graph?.actors.find((person) => person.id === actorId)?.role === "auditor" ? "auditor" : "agent";
    const params = new URLSearchParams({ actor_id: actorId, as_of: asOf, mode, view });
    api<CompanyGraph>(`/api/v1/company-graph?${params}`)
      .then((next) => {
        setGraph(next);
        setError("");
        setFocus((current) => current || (next.cases.some((item) => item.id === "C01") ? "C01" : next.cases[0]?.id ?? null));
        setSelected((current) => {
          const preferred = next.nodes.find((node) => node.label === "PO-C01-1123") || next.nodes.find((node) => node.case_id === "C01" && !node.redacted);
          if (current && next.nodes.some((node) => node.id === current)) return current;
          return preferred?.id ?? next.nodes[0]?.id ?? null;
        });
      })
      .catch((reason: Error) => setError(reason.message));
  }, [actorId, asOf, mode, job?.status, pulse]);

  useEffect(() => {
    if (!job || ["completed", "failed", "cancelled"].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`${API_URL}/api/v1/ingestions/${job.id}`);
      if (!response.ok) return;
      const next = (await response.json()) as IngestJob;
      setJob(next);
      setPulse((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  const shown = visibleGraph(graph, focus);
  const shownNodes = shown.nodes;
  const shownEdges = shown.edges;
  const node = shownNodes.find((item) => item.id === selected) || null;
  const activeCase = graph?.cases.find((item) => item.id === (focus || node?.case_id)) || null;
  const actors = graph?.actors.length ? graph.actors : [{ id: actorId, name: "Finance", role: "accountant" }];

  function openDocument(document: CompanyDocument) {
    if (document.case_id) setFocus(document.case_id);
    const match = graph?.nodes.find((item) => item.version_id === document.version_id || item.label === document.id);
    if (match) setSelected(match.id);
  }

  function chooseCase(caseId: string) {
    setFocus(caseId);
    const match = graph?.nodes.find((item) => item.case_id === caseId && item.type === "PURCHASE_ORDER" && !item.redacted)
      || graph?.nodes.find((item) => item.case_id === caseId && !item.redacted);
    if (match) setSelected(match.id);
  }

  return (
    <main className="workspace">
      <section className="stage">
        <div className="row toolbar">
          <div className="presets" role="group" aria-label="As of">
            {PRESETS.map((preset) => (
              <button key={preset.id} className={asOf === preset.asOf ? "primary" : ""} onClick={() => setAsOf(preset.asOf)}>
                {preset.label}
              </button>
            ))}
          </div>
          <label>
            Role{" "}
            <select aria-label="Actor" value={actorId} onChange={(event) => setActorId(event.target.value)}>
              {actors.map((person) => (
                <option key={person.id} value={person.id}>{person.name} · {person.role}</option>
              ))}
            </select>
          </label>
          <label>
            Mode{" "}
            <select aria-label="Scenario mode" value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="audit">Audit</option>
              <option value="pre_decision">Pre-decision</option>
            </select>
          </label>
          <button className="fab" aria-label="Add documents" onClick={() => setOpen(true)}>+</button>
        </div>
        {error ? <p className="bad">{error}</p> : null}
        {graph && graph.nodes.length === 0 ? (
          <div className="empty">
            <h2>The company graph is empty</h2>
            <p className="muted">Add documents to trace each amount to what was knowable at the time.</p>
          </div>
        ) : null}
        <EvidenceGraph nodes={shownNodes} edges={shownEdges} selected={selected} focused={focus} onSelect={setSelected} />
        <div className="legend">
          <span><i className="swatch supports" /> Supports</span>
          <span><i className="swatch reconciles" /> Reconciles</span>
          <span><i className="swatch contradicts" /> Contradicts</span>
          <span><i className="swatch governs" /> Governs</span>
        </div>
      </section>
      <aside className="panel inspector">
        <div className="eyebrow">Knowledge graph</div>
        <h2>{node?.redacted ? "Blocked" : node?.amount ? `$${node.amount}` : "Select a document"}</h2>
        <p className="muted">{graph ? `${graph.corpus.visible} company documents in this snapshot · ${graph.corpus.documents} in the file` : "Loading the company documents…"}</p>
        {graph?.run && graph.run.status !== "completed" ? <p className="warn">{graph.run.label || graph.run.status} · {graph.run.completed}/{graph.run.total}</p> : null}
        {activeCase ? (
          <div className="finding">
            <div className="eyebrow">{activeCase.id} · {activeCase.vendor}</div>
            <strong>{activeCase.title}</strong>
            <p>{activeCase.finding}</p>
            <a href={`/cases/${activeCase.id}`}>Open {activeCase.id}</a>
          </div>
        ) : null}
        <dl>
          <dt>Document</dt>
          <dd>{node?.redacted ? "Hidden until it is inside this snapshot" : node?.label || "—"}</dd>
          <dt>Relationship</dt>
          <dd>{graph?.edges.find((edge) => edge.source === selected || edge.target === selected)?.explanation || "None selected"}</dd>
          <dt>Available from</dt>
          <dd>{node?.available_from?.replace("T", " ").slice(0, 16) || "Unknown"}</dd>
          <dt>Source</dt>
          <dd>{node?.redacted ? "The amount and text stay hidden." : node?.text || "Choose a visible node."}</dd>
        </dl>
        <label className="doc-search">
          Company documents
          <input aria-label="Search company documents" value={query} placeholder="Search invoices, orders, emails" onChange={(event) => setQuery(event.target.value)} />
        </label>
        <div className="doc-list">
          {filteredDocuments(graph, query).map((document) => (
            <button key={document.version_id} onClick={() => openDocument(document)}>
              <strong>{document.id}</strong>
              <span>{document.amount ? `$${document.amount}` : document.kind}</span>
            </button>
          ))}
        </div>
        <div className="case-list">
          {(graph?.cases || []).map((item) => (
            <button key={item.id} className={focus === item.id ? "primary" : ""} onClick={() => chooseCase(item.id)}>
              <strong>{item.id}</strong>
              <span>{item.vendor}</span>
            </button>
          ))}
        </div>
      </aside>
      <IngestModal open={open} job={job} onClose={() => setOpen(false)} onStarted={(next) => { setJob(next); setOpen(true); }} />
    </main>
  );
}

function filteredDocuments(graph: CompanyGraph | null, query: string) {
  const needle = query.trim().toLowerCase();
  return (graph?.documents || [])
    .filter((document) => !needle || `${document.id} ${document.kind} ${document.title} ${document.case_id}`.toLowerCase().includes(needle))
    .slice(0, 40);
}

function visibleGraph(graph: CompanyGraph | null, focus: string | null) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!focus) return { nodes, edges };
  const ids = new Set(nodes.filter((node) => node.group === focus || node.case_id === focus).map((node) => node.id));
  for (const edge of edges) {
    const sourceIn = ids.has(edge.source);
    const targetIn = ids.has(edge.target);
    if (sourceIn === targetIn) continue;
    const other = nodes.find((node) => node.id === (sourceIn ? edge.target : edge.source));
    if (other?.group === "policy") ids.add(other.id);
  }
  return {
    nodes: nodes.filter((node) => ids.has(node.id)),
    edges: edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}
