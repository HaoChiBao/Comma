"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { DocumentPreview } from "@/components/DocumentPreview";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { NodeSummary, useNodeSummary } from "@/components/NodeSummary";
import { api, type ActorRow, type CaseRow, type GraphEdge, type GraphNode } from "@/lib/api";
import { accountantId } from "@/lib/caseActors";

export default function CasePage() {
  const params = useParams<{ id: string }>();
  const caseId = params.id;
  const [item, setItem] = useState<CaseRow | null>(null);
  const [actors, setActors] = useState<ActorRow[]>([]);
  const [actorId, setActorId] = useState("");
  const [mode, setMode] = useState("audit");
  const [day, setDay] = useState("2025-09-30");
  const [snapshotId, setSnapshotId] = useState("");
  const [hash, setHash] = useState("");
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [documentText, setDocumentText] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null);

  useEffect(() => {
    api<CaseRow>(`/api/v1/cases/${caseId}`)
      .then(async (row) => {
        setItem(row);
        const people = await api<ActorRow[]>("/api/v1/actors");
        const matched = people.filter((person) => person.organization_id === row.organization_id);
        setActors(matched);
        setActorId((current) => (matched.some((person) => person.id === current) ? current : accountantId(matched)));
      })
      .catch((reason: Error) => setError(reason.message));
  }, [caseId]);

  useEffect(() => {
    if (!actorId) return;
    const actor = actors.find((person) => person.id === actorId);
    const view = actor?.role === "auditor" ? "auditor" : "agent";
    api<{ id: string; manifest_hash: string }>("/api/v1/snapshots", {
      method: "POST",
      body: JSON.stringify({ case_id: caseId, actor_id: actorId, as_of: `${day}T23:59:00+00:00`, mode }),
    })
      .then(async (snapshot) => {
        setSnapshotId(snapshot.id);
        setHash(snapshot.manifest_hash);
        const graph = await api<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`/api/v1/snapshots/${snapshot.id}/graph?view=${view}`);
        setNodes(graph.nodes);
        setEdges(graph.edges);
        setError("");
        setSelected(graph.nodes[0]?.id ?? null);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [caseId, actorId, mode, day]);

  const current = nodes.find((node) => node.id === selected) || null;
  const actor = actors.find((person) => person.id === actorId);
  const nodeSummary = useNodeSummary(
    current?.id || null,
    actorId,
    `${day}T23:59:00+00:00`,
    mode,
    item?.organization_id || actor?.organization_id || "",
  );

  useEffect(() => {
    if (!current || current.redacted || !snapshotId) {
      setDocumentText(current?.redacted ? "The amount and text stay hidden until this evidence is inside the snapshot." : "");
      return;
    }
    api<{ text_excerpt: string }>(`/api/v1/snapshots/${snapshotId}/documents/${current.version_id}`)
      .then((document) => setDocumentText(document.text_excerpt))
      .catch((reason: Error) => setDocumentText(reason.message));
  }, [current, snapshotId]);

  return (
    <div className="shell">
      <header className="top">
        <div className="brand-row">
          <a href="/" className="brand-title">
            <img src="/comma-logo.png" alt="Comma" className="brand-logo-img" />
          </a>
          <span className="brand-tagline">Case Investigation · {caseId}</span>
        </div>
        <nav className="nav-links">
          <a href="/" className="nav-link">← Fullscreen Graph</a>
          <a href="/ingest" className="nav-link">Ingest</a>
        </nav>
      </header>
      <main>
        <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="eyebrow">{caseId}</div>
          <h2>{item?.vendor_name || "Case"}</h2>
          <p className="muted">{item?.summary}</p>
        </div>
        <div className="row">
          <a href={`/cases/${caseId}/replay`}>Run agent</a>
          <a href={`/cases/${caseId}/compare`}>Before / after</a>
        </div>
      </div>
      <div className="row" style={{ margin: "0.8rem 0" }}>
        <label>
          Date{" "}
          <input aria-label="As-of date" type="date" min="2025-09-01" max="2025-12-31" value={day} onChange={(event) => setDay(event.target.value)} />
        </label>
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
      </div>
      {mode === "pre_decision" ? <p className="warn">Pre-decision mode hides the recorded accrual from the agent.</p> : null}
      {actor?.role === "auditor" ? <p className="warn">Auditor view can show that later evidence exists. It does not reveal the hidden amount.</p> : <p className="muted">Agent view shows only what this snapshot allows.</p>}
      {error ? <p className="bad">{error}</p> : null}
      <div className="layout">
        <EvidenceGraph nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} />
        <aside className="panel inspector">
          <h2>{current?.redacted ? "Blocked" : current?.amount ? `$${current.amount}` : "Select a node"}</h2>
          {current ? (
            <NodeSummary summary={nodeSummary.summary} error={nodeSummary.error} nodes={nodes} onOpen={setSelected} />
          ) : null}
          <dl>
            <dt>Relationship</dt>
            <dd>{edges.find((edge) => edge.source === selected || edge.target === selected)?.type || "None selected"}</dd>
            <dt>Origin</dt>
            <dd>{current?.origin || "—"}</dd>
            <dt>Available from</dt>
            <dd>{current?.available_from || "Unknown"}</dd>
            <dt>Document date</dt>
            <dd>{current?.document_date || "Hidden"}</dd>
            <dt>Source</dt>
            <dd>
              {documentText || "Choose a visible node."}
              {current?.version_id && !current.redacted ? (
                <button className="preview-open" onClick={() => setPreviewId(current.version_id)}>Preview document</button>
              ) : null}
            </dd>
            <dt>Snapshot</dt>
            <dd>{hash ? hash.slice(0, 12) : "…"}</dd>
          </dl>
        </aside>
      </div>
      {previewId && actorId ? (
        <DocumentPreview
          versionId={previewId}
          actorId={actorId}
          asOf={`${day}T23:59:00+00:00`}
          mode={mode}
          organizationId={item?.organization_id}
          onClose={() => setPreviewId(null)}
        />
      ) : null}
    </main>
  </div>
  );
}
