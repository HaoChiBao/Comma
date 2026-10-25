"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { api, type CaseRow, type GraphEdge, type GraphNode } from "@/lib/api";

const ACTORS = [
  { id: "actor-maya", name: "Maya Chen", role: "accountant" },
  { id: "actor-jordan", name: "Jordan Hale", role: "auditor" },
];

export default function CasePage() {
  const params = useParams<{ id: string }>();
  const caseId = params.id;
  const [item, setItem] = useState<CaseRow | null>(null);
  const [actorId, setActorId] = useState("actor-maya");
  const [mode, setMode] = useState("audit");
  const [day, setDay] = useState("2025-09-30");
  const [snapshotId, setSnapshotId] = useState("");
  const [hash, setHash] = useState("");
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [documentText, setDocumentText] = useState("");

  useEffect(() => {
    api<CaseRow>(`/api/v1/cases/${caseId}`).then(setItem).catch((reason: Error) => setError(reason.message));
  }, [caseId]);

  useEffect(() => {
    const actor = ACTORS.find((person) => person.id === actorId);
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
            {ACTORS.map((person) => (
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
      {actorId === "actor-jordan" ? <p className="warn">Auditor view can show that later evidence exists. It does not reveal the hidden amount.</p> : <p className="muted">Agent view shows only what this snapshot allows.</p>}
      {error ? <p className="bad">{error}</p> : null}
      <div className="layout">
        <EvidenceGraph nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} />
        <aside className="panel inspector">
          <h2>{current?.redacted ? "Blocked" : current?.amount ? `$${current.amount}` : "Select a node"}</h2>
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
            <dd>{documentText || "Choose a visible node."}</dd>
            <dt>Snapshot</dt>
            <dd>{hash ? hash.slice(0, 12) : "…"}</dd>
          </dl>
        </aside>
      </div>
    </main>
  );
}
