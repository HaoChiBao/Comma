"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, Actor, api } from "../lib/api";

type TaskRow = {
  id: string;
  public_code: string;
  status: string;
  quantity: number | null;
  total_landed_cost: string | null;
  pending_action: string;
};

export default function HomePage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [actor, setActor] = useState<Actor | null>(null);
  const [phone, setPhone] = useState("+15555550100");
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [text, setText] = useState(
    "Build 100 complete desktop computers for our production run, within a $200,000 total component procurement budget, delivered by November 15.",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem("acme_token");
    const savedActor = window.localStorage.getItem("acme_actor");
    if (saved && savedActor) {
      setToken(saved);
      setActor(JSON.parse(savedActor) as Actor);
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    let stop = false;
    const load = () => {
      api<{ tasks: TaskRow[] }>("/api/tasks", token)
        .then((payload) => {
          if (!stop) setTasks(payload.tasks);
        })
        .catch((exc: Error) => setError(exc.message));
    };
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [token]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const response = await fetch(`${API_URL}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone_e164: phone }),
    });
    if (!response.ok) {
      setError("That phone is not an enrolled actor.");
      return;
    }
    const payload = await response.json();
    window.localStorage.setItem("acme_token", payload.token);
    window.localStorage.setItem("acme_actor", JSON.stringify(payload.actor));
    setToken(payload.token);
    setActor(payload.actor);
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!actor) return;
    setError(null);
    const response = await fetch(`${API_URL}/dev/inbound-message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender: actor.phone_e164,
        text,
        provider_message_id: `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.task_id) {
      setError(payload.detail || "The message did not open a task.");
      return;
    }
    router.push(`/tasks/${payload.task_id}`);
  }

  async function resetDemo() {
    await fetch(`${API_URL}/dev/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_demo_task: false }),
    });
    setTasks([]);
  }

  if (!token || !actor) {
    return (
      <main className="card" style={{ maxWidth: 460 }}>
        <h1>Acme procurement</h1>
        <p>Text a request. Get a costed plan. Approve it from iMessage.</p>
        <p className="muted">Sign in with an enrolled phone. Maya Chen is +15555550100.</p>
        <form onSubmit={login} className="grid">
          <label>
            Phone
            <input data-testid="login-phone" value={phone} onChange={(event) => setPhone(event.target.value)} />
          </label>
          <button data-testid="login-submit" type="submit">Sign in</button>
        </form>
        {error ? <p className="error">{error}</p> : null}
      </main>
    );
  }

  return (
    <main className="grid">
      <div className="top">
        <div>
          <h1>Requests</h1>
          <p className="muted">{actor.display_name} · {actor.role} · {actor.company_name}</p>
        </div>
        <button className="secondary" type="button" onClick={resetDemo}>Reset demo data</button>
      </div>
      <section className="card">
        <h2>New request</h2>
        <form onSubmit={send} className="grid">
          <textarea data-testid="composer-text" rows={4} value={text} onChange={(event) => setText(event.target.value)} />
          <div className="row">
            <button data-testid="composer-send" type="submit">Send as mock iMessage</button>
            <span className="muted">Sandbox only. Nothing is delivered to Apple.</span>
          </div>
        </form>
        {error ? <p className="error">{error}</p> : null}
      </section>
      <section className="card">
        <h2>Open work</h2>
        <ul data-testid="task-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <a data-testid="task-link" href={`/tasks/${task.id}`}>
                {task.public_code}
              </a>{" "}
              <span className="status">{task.status}</span>
              <div className="muted">{task.pending_action}{task.total_landed_cost ? ` · ${task.total_landed_cost}` : ""}</div>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
