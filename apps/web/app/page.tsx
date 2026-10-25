"use client";

import { useEffect, useState } from "react";
import { api, type CaseRow } from "@/lib/api";

export default function HomePage() {
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<CaseRow[]>("/api/v1/cases")
      .then(setCases)
      .catch(() => setError("The WorldState API is not reachable at localhost:8000."));
  }, []);

  return (
    <main>
      <p className="muted">
        Acme Corp closed September with a $150 accrual for Cloud Services Inc. In December the invoice was $158.
        Replay September 30 and the invoice is not evidence yet.
      </p>
      {error ? <p className="bad">{error}</p> : null}
      {!error && cases.length === 0 ? <p className="muted">Loading cases…</p> : null}
      <div className="grid">
        {cases.map((item) => (
          <a key={item.id} href={`/cases/${item.id}`} className="card">
            <div className="muted">{item.id} · {item.case_type.replaceAll("_", " ")}</div>
            <div className="amount">${item.recorded_amount}</div>
            <strong>{item.vendor_name}</strong>
            <p>{item.summary}</p>
            <span>Open historical replay</span>
          </a>
        ))}
      </div>
    </main>
  );
}
