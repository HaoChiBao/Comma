"use client";

import { useEffect, useRef, useState } from "react";
import { API_URL, api } from "@/lib/api";

export type PreviewEmail = {
  from?: string;
  date?: string;
  subject?: string;
  body?: string;
};

export type DocumentPreviewBody = {
  id: string;
  version_id: string;
  title: string;
  kind: string;
  amount: string | null;
  available_from: string | null;
  document_date: string | null;
  text: string;
  filename?: string | null;
  media_type?: string;
  original?: boolean;
  layout?: "pdf" | "email" | "table" | "image" | "text";
  email?: PreviewEmail | null;
  table?: { rows: string[][]; truncated?: boolean } | null;
};

export function DocumentPreview({
  versionId,
  actorId,
  asOf,
  mode,
  organizationId,
  onClose,
}: {
  versionId: string;
  actorId: string;
  asOf: string;
  mode: string;
  organizationId?: string;
  onClose: () => void;
}) {
  const [document, setDocument] = useState<DocumentPreviewBody | null>(null);
  const [error, setError] = useState("");
  const [fileUrl, setFileUrl] = useState("");
  const [fileFailed, setFileFailed] = useState(false);
  const fileUrlRef = useRef("");

  useEffect(() => {
    return () => {
      if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current);
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setDocument(null);
    setError("");
    setFileFailed(false);
    if (fileUrlRef.current) {
      URL.revokeObjectURL(fileUrlRef.current);
      fileUrlRef.current = "";
      setFileUrl("");
    }
    const params = new URLSearchParams({ actor_id: actorId, as_of: asOf, mode });
    if (organizationId) params.set("organization_id", organizationId);
    api<DocumentPreviewBody>(`/api/v1/company-graph/documents/${versionId}?${params}`)
      .then((body) => {
        if (!cancelled) setDocument(body);
      })
      .catch(() => {
        if (!cancelled) {
          setDocument(null);
          setError("This document is not available in this snapshot.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, actorId, asOf, mode, organizationId]);

  useEffect(() => {
    if (!document?.original || (document.layout !== "pdf" && document.layout !== "image")) return;
    let cancelled = false;
    const params = new URLSearchParams({ actor_id: actorId, as_of: asOf, mode });
    if (organizationId) params.set("organization_id", organizationId);
    const layout = document.layout;
    const mediaType = document.media_type;
    fetch(`${API_URL}/api/v1/company-graph/documents/${versionId}/file?${params}`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status));
        const buffer = await response.arrayBuffer();
        const type = layout === "pdf" ? "application/pdf" : mediaType || "application/octet-stream";
        const url = URL.createObjectURL(new Blob([buffer], { type }));
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current);
        fileUrlRef.current = url;
        setFileUrl(url);
        setFileFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFileFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [document, versionId, actorId, asOf, mode, organizationId]);

  const expanded = document?.layout === "pdf" || document?.layout === "image";
  const showMissing = Boolean(document) && (document?.original !== true || fileFailed);

  return (
    <div className="modal-back" role="presentation" onClick={onClose}>
      <article
        className={`modal preview-sheet${expanded ? " expanded" : ""}`}
        role="dialog"
        aria-labelledby="preview-title"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="preview-head">
          <div>
            <div className="eyebrow">{document?.kind || "Document"}</div>
            <h2 id="preview-title">{error ? "Document preview" : document?.title || document?.id || "Document preview"}</h2>
          </div>
          <button type="button" onClick={onClose}>Close</button>
        </div>
        {error ? <p className="bad">{error}</p> : null}
        {document ? (
          <>
            <dl className="preview-meta">
              <div><dt>Document</dt><dd>{document.id}</dd></div>
              <div><dt>Amount</dt><dd>{document.amount ? `$${document.amount}` : "—"}</dd></div>
              <div><dt>Document date</dt><dd>{document.document_date?.slice(0, 10) || "—"}</dd></div>
              <div><dt>Available from</dt><dd>{document.available_from?.replace("T", " ").slice(0, 16) || "—"}</dd></div>
            </dl>
            {showMissing ? <p className="preview-missing">Original file is not in this snapshot.</p> : null}
            <PreviewBody document={document} fileUrl={fileUrl} fileFailed={fileFailed} />
          </>
        ) : error ? null : (
          <p className="muted">Opening the document…</p>
        )}
      </article>
    </div>
  );
}

function PreviewBody({
  document,
  fileUrl,
  fileFailed,
}: {
  document: DocumentPreviewBody;
  fileUrl: string;
  fileFailed: boolean;
}) {
  if ((document.layout === "pdf" || document.layout === "image") && document.original && !fileFailed) {
    if (!fileUrl) return <p className="muted">Opening the document…</p>;
    if (document.layout === "pdf") return <iframe className="preview-frame" src={fileUrl} title={document.title} />;
    return (
      <div className="preview-stage">
        <img className="preview-image" src={fileUrl} alt={document.title} />
      </div>
    );
  }
  if (document.layout === "email" && document.email) return <EmailView email={document.email} />;
  if (document.layout === "table" && document.table?.rows?.length) return <TableView table={document.table} />;
  return <Prose text={document.text} />;
}

function EmailView({ email }: { email: PreviewEmail }) {
  const rows = [
    email.from ? (["From", email.from] as const) : null,
    email.date ? (["Date", email.date] as const) : null,
    email.subject ? (["Subject", email.subject] as const) : null,
  ].filter((row) => row !== null);
  return (
    <div className="preview-email">
      {rows.length ? (
        <dl>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {email.body ? <Prose text={email.body} /> : null}
    </div>
  );
}

function TableView({ table }: { table: { rows: string[][]; truncated?: boolean } }) {
  const [head, ...rest] = table.rows;
  const header = rest.length ? head : null;
  const data = rest.length ? rest : table.rows;
  return (
    <>
      <div className="preview-table-wrap">
        <table className="preview-table">
          {header ? (
            <thead>
              <tr>
                {header.map((cell, index) => (
                  <th key={index}>{cell}</th>
                ))}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {data.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.truncated ? <p className="preview-note">Showing the first rows stored in this file.</p> : null}
    </>
  );
}

function Prose({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  if (!blocks.length) return <p className="muted">This document has no extracted text.</p>;
  return (
    <div className="preview-prose">
      {blocks.map((block, index) => {
        const heading = /^(#{1,3})\s+(\S.*)$/.exec(block);
        if (heading && !block.includes("\n")) {
          if (heading[1].length === 1) return <h3 key={index}>{heading[2]}</h3>;
          return <h4 key={index}>{heading[2]}</h4>;
        }
        return <p key={index}>{block}</p>;
      })}
    </div>
  );
}
