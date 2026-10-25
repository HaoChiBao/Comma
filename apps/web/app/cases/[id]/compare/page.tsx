"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { accountantId, actorsForCase } from "@/lib/caseActors";

type Compare = {
  constrained: { finding: { summary: string; finding: string; blocked_access_attempts: number } };
  unrestricted: { summary: string; warning: string; leaked_version_ids: string[] };
  leakage: string[];
};

export default function ComparePage() {
  const params = useParams<{ id: string }>();
  const [actorId, setActorId] = useState("");
  const [result, setResult] = useState<Compare | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    actorsForCase(params.id)
      .then((people) => setActorId(accountantId(people)))
      .catch((reason: Error) => setError(reason.message));
  }, [params.id]);

  async function run() {
    if (!actorId) return;
    setError("");
    try {
      setResult(
        await api<Compare>("/api/v1/evaluations/compare", {
          method: "POST",
          body: JSON.stringify({
            case_id: params.id,
            actor_id: actorId,
            as_of: "2025-09-30T23:59:00+00:00",
            mode: "audit",
          }),
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Compare failed");
    }
  }

  return (
    <main>
      <a href={`/cases/${params.id}`}>Back to the graph</a>
      <h2>Unsafe baseline versus WorldState</h2>
      <p className="warn">The unrestricted column is an intentionally unsafe baseline. It is never used to propose a financial action.</p>
      <button className="primary" onClick={run}>Compare September 30</button>
      {error ? <p className="bad">{error}</p> : null}
      {result ? (
        <div className="split" style={{ marginTop: "1rem" }}>
          <section className="panel">
            <h2>WorldState</h2>
            <p className="ok">{result.constrained.finding.finding}</p>
            <p>{result.constrained.finding.summary}</p>
            <p>Blocked reads: {result.constrained.finding.blocked_access_attempts}</p>
          </section>
          <section className="panel">
            <h2>Unrestricted</h2>
            <p className="bad">{result.unrestricted.warning}</p>
            <p>{result.unrestricted.summary}</p>
            <p>Leaked versions: {result.leakage.join(", ") || "none"}</p>
          </section>
        </div>
      ) : null}
    </main>
  );
}
