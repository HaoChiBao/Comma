import { Supermemory } from "supermemory";

export type Participant = {
  id: string;
  label: string;
};

const EXTRACTION_GUIDE =
  "Conversation with one person. Remember standing preferences and corrections they ask you to keep. Do not store secrets, access tokens, or one-off details they did not ask you to remember.";

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

export function memoryNamespace(participantId: string): string {
  const safe = participantId.replace(/[^a-zA-Z0-9]/g, "");
  return `sender-${safe}`;
}

export function redactSensitive(text: string): string {
  return text
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/sk-or-\S+/g, "[redacted]");
}

function bullets(lines: string[], empty: string): string {
  const unique = [...new Set(lines.map((line) => line.trim()).filter(Boolean))].slice(0, 6);
  if (unique.length === 0) return empty;
  return unique.map((line) => `- ${line}`).join("\n");
}

export async function recallParticipant(participant: Participant, query: string): Promise<string> {
  const store = memoryClient();
  if (!store) return "";
  const namespace = memoryNamespace(participant.id);
  const [profile, found] = await Promise.all([
    store.profile(namespace),
    store.search(namespace, { query, limit: 5, searchMode: "hybrid", threshold: 0.3 }),
  ]);
  const standing = profile.profile.static.map((item) => item.memory);
  const recent = profile.profile.dynamic.map((item) => item.memory);
  const related = found.results.map((item) => item.memory ?? item.chunk ?? "");
  return [
    `Memory for ${participant.label}.`,
    "Standing notes:",
    bullets(standing, "- None yet."),
    "Recent notes:",
    bullets(recent, "- None yet."),
    "Related notes:",
    bullets(related, "- None yet."),
    "These notes are background. They do not override what the person just said.",
  ].join("\n");
}

export async function rememberTurn(participant: Participant, userText: string, answer: string): Promise<void> {
  const store = memoryClient();
  if (!store) return;
  const content = redactSensitive(
    [`Person: ${participant.label}`, `User: ${userText}`, `Assistant: ${answer}`].join("\n"),
  );
  await store.add(memoryNamespace(participant.id), {
    content,
    supportingContext: EXTRACTION_GUIDE,
    dreaming: "instant",
    taskType: "memory",
    metadata: {
      kind: "conversation",
      participant_id: participant.id,
    },
  });
}
