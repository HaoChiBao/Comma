"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

type Step = { id: string; name: string; label: string; status: string; error: string; detail: Record<string, unknown> };
type Transaction = { id: string; kind: string; amount: string | null; available_from: string; episode_id: string };
type Job = { id: string; status: string; error: string; steps: Step[]; transactions: Transaction[] };

export default function IngestPage() {
  const [files, setFiles] = useState<FileList | null>(null);
  const [day, setDay] = useState("2025-09-30");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!job || ["completed", "failed", "cancelled"].includes(job.status)) return;
    const timer = window.setInterval(() => {
      refresh(job.id);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  async function refresh(id: string) {
    const response = await fetch(`${API_URL}/api/v1/ingestions/${id}`);
    if (response.ok) setJob(await response.json());
  }

  async function start(sample: boolean) {
    setBusy(true);
    setError("");
    try {
      const response = sample
        ? await fetch(`${API_URL}/api/v1/ingestions/decagon`, { method: "POST" })
        : await sendFiles();
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Upload failed");
      setJob(body);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendFiles() {
    const form = new FormData();
    form.set("available_from", day);
    form.set("organization_id", "org-decagon");
    Array.from(files || []).forEach((file) => form.append("files", file));
    return fetch(`${API_URL}/api/v1/ingestions`, { method: "POST", body: form });
  }

  async function control(action: "pause" | "resume" | "cancel") {
    if (!job) return;
    const response = await fetch(`${API_URL}/api/v1/ingestions/${job.id}/${action}`, { method: "POST" });
    const body = await response.json();
    if (!response.ok) setError(body.detail || "Could not update the run");
    else setJob(body);
  }

  const cases = job?.steps.find((step) => step.name === "cases")?.detail?.cases;
  const caseIds = Array.isArray(cases) ? cases.map(String) : [];

  return (
    <div className="shell">
      <header className="top">
        <div className="brand-row">
          <a href="/" className="brand-title">
            <img src="/comma-logo.png" alt="Comma" className="brand-logo-img" />
          </a>
          <span className="brand-tagline">Ingest Documents</span>
        </div>
        <nav className="nav-links">
          <a href="/" className="nav-link">← Fullscreen Graph</a>
        </nav>
      </header>
      <main>
        <h2>Read documents into the graph</h2>
      <p className="muted">
        Upload one document or a set. Each transaction is its own step, so a pause, a stop, or a failure resumes at that step instead of starting over.
        A received date in the text is when the document becomes evidence. The date on the document is not.
      </p>
      <div className="row" style={{ margin: "0.8rem 0" }}>
        <label>
          Available from{" "}
          <input aria-label="Available from" type="date" value={day} onChange={(event) => setDay(event.target.value)} />
        </label>
        <input aria-label="Documents" type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.eml" onChange={(event) => setFiles(event.target.files)} />
        <button className="primary" disabled={busy || !files?.length} onClick={() => start(false)}>Start ingestion</button>
        <button disabled={busy} onClick={() => start(true)}>Load the company documents</button>
      </div>
      {error ? <p className="bad">{error}</p> : null}
      {job ? (
        <section className="panel">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Run {job.status}</h2>
            <div className="row">
              <button disabled={!["running", "queued"].includes(job.status)} onClick={() => control("pause")}>Pause</button>
              <button disabled={!["paused", "failed"].includes(job.status)} onClick={() => control("resume")}>Resume</button>
              <button disabled={["completed", "cancelled"].includes(job.status)} onClick={() => control("cancel")}>Stop</button>
            </div>
          </div>
          {job.error ? <p className="bad">{job.error}</p> : null}
          <ol className="steps">
            {job.steps.map((step) => (
              <li key={step.id} className={step.status}>
                <strong>{step.label}</strong>
                <span>{step.status}</span>
                {step.error ? <small>{step.error}</small> : null}
              </li>
            ))}
          </ol>
          {job.transactions.length ? (
            <ul>
              {job.transactions.map((item) => (
                <li key={item.id}>{item.id} · {item.kind} · {item.amount ? `$${item.amount}` : "no amount"} · available {item.available_from.slice(0, 10)}</li>
              ))}
            </ul>
          ) : null}
          {caseIds.map((id) => (
            <p key={id}><a href={`/cases/${id}`}>Open {id}</a></p>
          ))}
          <p className="muted">Episodes are written with Graphiti when GRAPHITI_URI is set. Otherwise they are stored in Graphiti’s episode shape inside WorldState, and the same step retries against Graphiti after you configure it.</p>
        </section>
      ) : null}
    </main>
  </div>
  );
}
