"use client";

import { useState } from "react";
import { API_URL } from "@/lib/api";

type Step = { id: string; name: string; label: string; status: string; error: string };
export type IngestJob = { id: string; status: string; error: string; steps: Step[] };

export function IngestModal({
  open,
  job,
  onClose,
  onStarted,
}: {
  open: boolean;
  job: IngestJob | null;
  onClose: () => void;
  onStarted: (job: IngestJob) => void;
}) {
  const [files, setFiles] = useState<FileList | null>(null);
  const [day, setDay] = useState("2025-09-30");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) return null;
  const running = job && !["completed", "failed", "cancelled"].includes(job.status);
  const done = job?.steps.filter((step) => step.status === "completed").length || 0;

  async function startUpload() {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.set("available_from", day);
      form.set("organization_id", "org-decagon");
      Array.from(files || []).forEach((file) => form.append("files", file));
      const response = await fetch(`${API_URL}/api/v1/ingestions`, { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Upload failed");
      onStarted(body);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function startCompany() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/ingestions/decagon`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "The company documents could not be read");
      onStarted(body);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The company documents could not be read");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-back" role="presentation" onClick={onClose}>
      <div className="modal panel" role="dialog" aria-modal="true" aria-labelledby="ingest-title" onClick={(event) => event.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 id="ingest-title">Add documents</h2>
          <button aria-label="Close" onClick={onClose}>Close</button>
        </div>
        <p className="muted">
          Upload one document or a set. A received date in the text is when it becomes evidence. The date printed on it is not.
        </p>
        <div className="stack">
          <label>
            Available from
            <input aria-label="Available from" type="date" value={day} onChange={(event) => setDay(event.target.value)} />
          </label>
          <input aria-label="Documents" type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.eml" onChange={(event) => setFiles(event.target.files)} />
          <button className="primary" disabled={busy || !files?.length} onClick={startUpload}>Ingest upload</button>
        </div>
        <div className="divider" />
        <h2>Decagon company documents</h2>
        <p className="muted">
          Fictional finance simulation. 517 source documents, ten accrual investigations, and the relationships between purchase orders, estimates, accruals, and later invoices.
        </p>
        <button className="primary" disabled={busy || Boolean(running) || job?.status === "completed"} onClick={startCompany}>
          {job?.status === "completed" ? "Company documents are loaded" : "Run the full analysis"}
        </button>
        {error ? <p className="bad">{error}</p> : null}
        {job ? (
          <div className="progress-block">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{job.status}</strong>
              <span className="muted">{job.steps.length ? `${done}/${job.steps.length}` : "Starting"}</span>
            </div>
            <div className="bar" aria-hidden="true">
              <span style={{ width: `${job.steps.length ? (done / job.steps.length) * 100 : 8}%` }} />
            </div>
            {job.error ? <p className="bad">{job.error}</p> : null}
            <ol className="steps">
              {job.steps.slice(-8).map((step) => (
                <li key={step.id} className={step.status}>
                  <strong>{step.label}</strong>
                  <span>{step.status}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>
    </div>
  );
}
