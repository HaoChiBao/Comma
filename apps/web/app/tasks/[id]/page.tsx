"use client";

import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { API_URL, api } from "../../../lib/api";

type Detail = {
  task: {
    id: string;
    public_code: string;
    status: string;
    quantity: number | null;
    budget_amount: string | null;
    destination: string | null;
    completion_label: string | null;
    blocked_reason: string | null;
    pending_action: string;
  };
  assumptions: string[];
  bom: {
    readiness_computers: number;
    lines: Array<{
      component_type: string;
      required_sku: string;
      units_per_computer: number;
      allocations: Array<{ supplier_name: string; sku: string; quantity: number; unit_price: string }>;
    }>;
  };
  plans: Array<{
    id: string;
    version: number;
    status: string;
    total_landed_cost: string | null;
    readiness_computers: number;
    explanation: string | null;
    budget_headroom: string | null;
    lines: Array<{ component_type: string; sku: string; supplier_name: string; quantity: number; unit_price: string }>;
  }>;
  approvals: Array<{ id: string; status: string; token: string }>;
  pending_approval: { id: string; status: string; token: string } | null;
  orders: Array<{ po_number: string; supplier_name: string; amount: string; status: string }>;
  rfqs: Array<{ supplier_name: string; correlation_token: string; status: string }>;
  quotes: Array<{ supplier_name: string; sku: string; status: string; unit_price: string | null; available_qty: number | null }>;
  events: Array<{ id: string; action: string; reason: string | null; allowed: boolean; evidence_refs: string[] }>;
};

type SandboxRow = { id: string; kind: string; status: string; payload: { body?: string; text?: string; to?: string; subject?: string } };

function currentPlan(detail: Detail) {
  return [...detail.plans].reverse().find((plan) => ["proposed", "approved", "executed"].includes(plan.status)) || null;
}

export default function TaskPage() {
  const params = useParams<{ id: string }>();
  const [token, setToken] = useState<string | null>(null);
  const [phone, setPhone] = useState("+15555550100");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [sandbox, setSandbox] = useState<SandboxRow[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setToken(window.localStorage.getItem("acme_token"));
    const actor = window.localStorage.getItem("acme_actor");
    if (actor) setPhone(JSON.parse(actor).phone_e164);
  }, []);

  useEffect(() => {
    if (!token) return;
    let stop = false;
    const load = () => {
      api<Detail>(`/api/tasks/${params.id}`, token)
        .then((payload) => {
          if (!stop) setDetail(payload);
        })
        .catch((exc: Error) => setError(exc.message));
      api<{ sandbox: SandboxRow[] }>(`/dev/tasks/${params.id}/sandbox`, token)
        .then((payload) => {
          if (!stop) setSandbox(payload.sandbox);
        })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [token, params.id]);

  async function mutate(path: string, body?: unknown) {
    if (!token) return;
    setError(null);
    const response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!response.ok) setError(await response.text());
    const payload = await api<Detail>(`/api/tasks/${params.id}`, token);
    setDetail(payload);
  }

  async function sendReply(event: FormEvent) {
    event.preventDefault();
    const response = await fetch(`${API_URL}/dev/inbound-message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender: phone,
        text: reply,
        provider_message_id: `ui-reply-${Date.now()}`,
      }),
    });
    if (!response.ok) setError(await response.text());
    setReply("");
    if (token) setDetail(await api<Detail>(`/api/tasks/${params.id}`, token));
  }

  if (!detail) return <main><p className="muted">Loading the task…</p>{error ? <p className="error">{error}</p> : null}</main>;
  const plan = currentPlan(detail);
  const approval = detail.pending_approval;

  return (
    <main className="grid">
      <div className="top">
        <div>
          <p className="muted"><a href="/">All requests</a></p>
          <h1>{detail.task.public_code}</h1>
          <p>
            <span className="status" data-testid="task-status">{detail.task.status}</span>{" "}
            <span className="muted">{detail.task.pending_action}</span>
          </p>
          {detail.task.completion_label ? <p data-testid="completion-label">{detail.task.completion_label}</p> : null}
          {detail.task.blocked_reason ? <p className="error">{detail.task.blocked_reason}</p> : null}
        </div>
      </div>
      <section className="metrics">
        <div className="metric"><span className="muted">Quantity</span><strong>{detail.task.quantity ?? "—"}</strong></div>
        <div className="metric"><span className="muted">Budget</span><strong>{detail.task.budget_amount ?? "—"}</strong></div>
        <div className="metric"><span className="muted">Landed cost</span><strong data-testid="plan-total">{plan?.total_landed_cost ?? "—"}</strong></div>
        <div className="metric"><span className="muted">Readiness</span><strong data-testid="plan-readiness">{plan ? `${plan.readiness_computers} of ${detail.task.quantity}` : `${detail.bom.readiness_computers} of ${detail.task.quantity ?? 0}`}</strong></div>
        <div className="metric"><span className="muted">Plan</span><strong data-testid="plan-version">{plan ? `v${plan.version}` : "—"}</strong></div>
        <div className="metric"><span className="muted">Plan status</span><strong data-testid="plan-status">{plan?.status ?? "none"}</strong></div>
      </section>
      <div className="cards">
        <section className="card">
          <h2>Bill of materials</h2>
          <p className="muted">Ship to {detail.task.destination}. {detail.assumptions.join(" ")}</p>
          <ul className="tree" data-testid="bom-tree">
            {detail.bom.lines.map((line) => (
              <li key={line.component_type}>
                <strong>{line.component_type}</strong> · {line.units_per_computer}/computer · {line.required_sku}
                {line.allocations.map((allocation) => (
                  <div className="alloc" key={`${allocation.supplier_name}-${allocation.sku}`}>
                    {allocation.quantity} × {allocation.sku} from {allocation.supplier_name} @ {allocation.unit_price}
                  </div>
                ))}
              </li>
            ))}
          </ul>
          {plan?.explanation ? <p>{plan.explanation}</p> : null}
          {plan?.budget_headroom ? <p className="muted">Headroom {plan.budget_headroom}</p> : null}
        </section>
        <section className="card">
          <h2>Approval</h2>
          <p data-testid="approval-status">
            {approval ? `${approval.status} ${approval.token}` : detail.approvals.at(-1) ? `${detail.approvals.at(-1)?.status} ${detail.approvals.at(-1)?.token}` : "No approval yet"}
          </p>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button data-testid="btn-simulate-replies" type="button" onClick={() => mutate(`/dev/tasks/${params.id}/simulate-replies`)}>Simulate supplier replies</button>
            <button className="secondary" data-testid="btn-disrupt-ram" type="button" onClick={() => mutate(`/dev/tasks/${params.id}/disrupt-ram`, { reduce_by: 50 })}>Disrupt RAM −50</button>
            <button
              data-testid="btn-approve"
              type="button"
              disabled={!approval}
              onClick={() => {
                if (!approval) return;
                setReply(`APPROVE ${approval.token}`);
              }}
            >
              Fill mock iMessage approval
            </button>
            <button className="secondary" data-testid="btn-ack" type="button" onClick={() => mutate(`/dev/tasks/${params.id}/acknowledge-orders`)}>Acknowledge orders</button>
          </div>
          <form onSubmit={sendReply} className="grid" style={{ marginTop: "0.8rem" }}>
            <label>
              Mock iMessage reply
              <input data-testid="reply-text" value={reply} onChange={(event) => setReply(event.target.value)} placeholder="APPROVE APV-…" />
            </label>
            <button data-testid="reply-send" type="submit">Send reply</button>
          </form>
          {error ? <p className="error">{error}</p> : null}
        </section>
      </div>
      <section className="card">
        <h2>Purchase orders</h2>
        <table data-testid="po-list">
          <thead><tr><th>PO</th><th>Supplier</th><th>Amount</th><th>Status</th></tr></thead>
          <tbody>
            {detail.orders.map((order) => (
              <tr key={order.po_number}>
                <td>{order.po_number}</td>
                <td>{order.supplier_name}</td>
                <td>{order.amount}</td>
                <td>{order.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <div className="cards">
        <section className="card">
          <h2>RFQs</h2>
          <ul data-testid="rfq-list">
            {detail.rfqs.map((rfq) => (
              <li key={rfq.correlation_token}>{rfq.supplier_name} · {rfq.status} · {rfq.correlation_token}</li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h2>Sandbox inbox</h2>
          <p className="muted">Simulated mail. Nothing left this machine.</p>
          <ul data-testid="sandbox-inbox">
            {sandbox.map((row) => (
              <li key={row.id}>
                <strong>{row.kind}</strong> · {row.status}
                <pre style={{ whiteSpace: "pre-wrap" }}>{row.payload.body || row.payload.text}</pre>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <section className="card">
        <h2>Quotes</h2>
        <table data-testid="quote-table">
          <thead><tr><th>Supplier</th><th>SKU</th><th>Status</th><th>Price</th><th>Qty</th></tr></thead>
          <tbody>
            {detail.quotes.map((quote) => (
              <tr key={`${quote.supplier_name}-${quote.sku}-${quote.status}-${quote.unit_price}`}>
                <td>{quote.supplier_name}</td>
                <td>{quote.sku}</td>
                <td>{quote.status}</td>
                <td>{quote.unit_price ?? "—"}</td>
                <td>{quote.available_qty ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Audit</h2>
        <ul data-testid="audit-log">
          {detail.events.map((event) => (
            <li key={event.id}>
              <strong>{event.action}</strong> {event.allowed ? "" : "(denied)"} — {event.reason}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
