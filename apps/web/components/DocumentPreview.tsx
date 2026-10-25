"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type DocumentPreviewBody = {
  id: string;
  version_id: string;
  title: string;
  kind: string;
  amount: string | null;
  available_from: string | null;
  document_date: string | null;
  text: string;
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

  useEffect(() => {
    const params = new URLSearchParams({ actor_id: actorId, as_of: asOf, mode });
    if (organizationId) params.set("organization_id", organizationId);
    api<DocumentPreviewBody>(`/api/v1/company-graph/documents/${versionId}?${params}`)
      .then((body) => {
        setDocument(body);
        setError("");
      })
      .catch(() => {
        setDocument(null);
        setError("This document is not available in this snapshot.");
      });
  }, [versionId, actorId, asOf, mode, organizationId]);

  return (
    <div className="modal-back" role="presentation" onClick={onClose}>
      <article className="modal preview-sheet" role="dialog" aria-labelledby="preview-title" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="row">
          <div>
            <div className="eyebrow">{document?.kind || "Document"}</div>
            <h2 id="preview-title">{document?.title || document?.id || "Document preview"}</h2>
          </div>
          <button onClick={onClose}>Close</button>
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
            <pre className="preview-text">{document.text}</pre>
          </>
        ) : error ? null : <p className="muted">Opening the document…</p>}
      </article>
    </div>
  );
}
