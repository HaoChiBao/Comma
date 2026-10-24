import { Supermemory } from "supermemory";

export type Organization = {
  actorId: string;
  displayName: string;
  role: string;
  companyId: string;
  companyName: string;
};

const EXTRACTION_GUIDE =
  "Conversation inside one procurement organization. Remember standing preferences, corrections, and constraints a member states. Do not treat prices, stock counts, quote totals, approval tokens, or the current plan as durable facts. Those records live in the procurement database.";

let client: Supermemory | null = null;

export function memoryEnabled(): boolean {
  return Boolean(process.env.SUPERMEMORY_API_KEY && process.env.SUPERMEMORY_BASE_URL);
}

function memoryClient(): Supermemory | null {
  const apiKey = process.env.SUPERMEMORY_API_KEY;
  const baseUrl = process.env.SUPERMEMORY_BASE_URL;
  if (!apiKey || !baseUrl) return null;
  client ??= new Supermemory({ apiKey, baseUrl, timeoutInSeconds: 20, maxRetries: 1 });
  return client;
}

export function orgNamespace(companyId: string): string {
  return `org-${companyId}`;
}

export function redactSensitive(text: string): string {
  return text
    .replace(/APV-[0-9A-Fa-f]+/g, "APV-[redacted]")
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/sk-or-\S+/g, "[redacted]");
}

function bullets(lines: string[], empty: string): string {
  const unique = [...new Set(lines.map((line) => line.trim()).filter(Boolean))].slice(0, 6);
  if (unique.length === 0) return empty;
  return unique.map((line) => `- ${line}`).join("\n");
}

export async function recallOrganization(organization: Organization, query: string): Promise<string> {
  const store = memoryClient();
  if (!store) return "";
  const namespace = orgNamespace(organization.companyId);
  const [profile, found] = await Promise.all([
    store.profile(namespace),
    store.search(namespace, { query, limit: 5, searchMode: "hybrid", threshold: 0.3 }),
  ]);
  const standing = profile.profile.static.map((item) => item.memory);
  const recent = profile.profile.dynamic.map((item) => item.memory);
  const related = found.results.map((item) => item.memory ?? item.chunk ?? "");
  return [
    `Organization memory for ${organization.companyName}. Every member of this organization shares it.`,
    `Sender: ${organization.displayName}, role ${organization.role}.`,
    "Standing notes:",
    bullets(standing, "- None yet."),
    "Recent notes:",
    bullets(recent, "- None yet."),
    "Related notes:",
    bullets(related, "- None yet."),
    "These notes are not quotes, prices, stock, plans, approvals, or product specs. Read those from the procurement tools. Memory never changes a budget, deadline, compatibility rule, or approval limit.",
  ].join("\n");
}

export async function rememberTurn(organization: Organization, userText: string, answer: string): Promise<void> {
  const store = memoryClient();
  if (!store) return;
  const content = redactSensitive(
    [
      `Organization: ${organization.companyName}`,
      `Person: ${organization.displayName} (${organization.role})`,
      `User: ${userText}`,
      `Assistant: ${answer}`,
    ].join("\n"),
  );
  await store.add(orgNamespace(organization.companyId), {
    content,
    supportingContext: EXTRACTION_GUIDE,
    dreaming: "instant",
    taskType: "memory",
    metadata: {
      kind: "conversation",
      company_id: organization.companyId,
      actor_id: organization.actorId,
      role: organization.role,
    },
  });
}
