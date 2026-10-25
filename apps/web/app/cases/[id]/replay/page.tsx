"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, type ActorRow } from "@/lib/api";
import { accountantId, actorsForCase, auditorId } from "@/lib/caseActors";

type Detail = {
  finding: {
    finding: string;
    summary: string;
    observations: { text: string; source_version_id: string }[];
    missing_information: string[];
    uncertainties: string[];
    blocked_access_attempts: number;
  };
  tool_events: { id: string; tool_name: string; blocked_reason: string | null; params: { source_version_id?: string } }[];
  proposal: { id: string; action_type: string; rationale: string; status: string } | null;
};

export default function ReplayPage() {
  const params = useParams<{ id: string }>();
  const [actors, setActors] = useState<ActorRow[]>([]);
  const [actorId, setActorId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    actorsForCase(params.id)
      .then((people) => {
        setActors(people);
        setActorId(accountantId(people));
      })
      .catch((reason: Error) => setError(reason.message));
  }, [params.id]);

  async function run() {
    setError("");
    try {
      const snapshot = await api<{ id: string }>("/api/v1/snapshots", {
        method: "POST",
        body: JSON.stringify({
          case_id: params.id,
          actor_id: actorId,
          as_of: "2025-09-30T23:59:00+00:00",
          mode: "audit",
        }),
      });
      setDetail(await api<Detail>("/api/v1/investigations", { method: "POST", body: JSON.stringify({ snapshot_id: snapshot.id }) }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Run failed");
    }
  }

  async function review(decision: "approve" | "reject") {
    if (!detail?.proposal) return;
    setError("");
    try {
      const result = await api<{ status: string }>(`/api/v1/proposals/${detail.proposal.id}/review`, {
        method: "POST",
        body: JSON.stringify({ actor_id: auditorId(actors), decision, comment: "Reviewed against the company documents." }),
      });
      setMessage(`Proposal ${result.status}. The agent did not post an entry.`);
      setDetail({ ...detail, proposal: { ...detail.proposal, status: result.status } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Review failed");
    }
  }

  return (
    <div className="shell">
      <header className="top">
        <div className="brand-row">
          <a href="/" className="brand-title">Comma</a>
          <span className="brand-tagline">Agent Replay · {params.id}</span>
        </div>
        <nav className="nav-links">
          <a href={`/cases/${params.id}`} className="nav-link">← Case View</a>
          <a href="/" className="nav-link">Fullscreen Graph</a>
        </nav>
      </header>
      <main>
        <h2>Agent replay</h2>
      <div className="row">
        <label>
          Investigator{" "}
          <select aria-label="Investigator" value={actorId} onChange={(event) => setActorId(event.target.value)}>
            {actors.map((person) => (
              <option key={person.id} value={person.id}>{person.name} · {person.role}</option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={run}>Run agent at 30 September 2025</button>
      </div>
      {error ? <p className="bad">{error}</p> : null}
      {detail ? (
        <div className="layout" style={{ marginTop: "1rem" }}>
          <section className="panel">
            <h2>{detail.finding.finding}</h2>
            <p>{detail.finding.summary}</p>
            {detail.finding.observations.map((item) => (
              <p key={item.source_version_id}>{item.text} <span className="muted">({item.source_version_id})</span></p>
            ))}
            {detail.finding.uncertainties.map((item) => <p key={item} className="warn">{item}</p>)}
            {detail.finding.missing_information.map((item) => <p key={item} className="muted">{item}</p>)}
            <p>Blocked reads: {detail.finding.blocked_access_attempts}</p>
            {detail.tool_events.map((event) => (
              <div key={event.id} className="event">
                <strong>{event.tool_name}</strong> {event.params.source_version_id}{" "}
                {event.blocked_reason ? <span className="bad">{event.blocked_reason}</span> : <span className="ok">read</span>}
              </div>
            ))}
          </section>
          <aside className="panel">
            <h2>Proposed action</h2>
            <p>{detail.proposal?.action_type}</p>
            <p>{detail.proposal?.rationale}</p>
            <p className="muted">Status: {detail.proposal?.status}</p>
            <div className="row">
              <button className="primary" onClick={() => review("approve")}>Approve</button>
              <button onClick={() => review("reject")}>Reject</button>
            </div>
            <p className="muted">Only the auditor can approve. Nothing is posted to an ERP.</p>
            {message ? <p className="ok">{message}</p> : null}
          </aside>
        </div>
      ) : <p className="muted">The offline investigator uses the snapshot. It has no web access and no model key.</p>}
    </main>
  </div>
  );
}
