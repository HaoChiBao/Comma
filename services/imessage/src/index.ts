import { ChatOpenRouter } from "@langchain/openrouter";
import { MemorySaver } from "@langchain/langgraph";
import { createDeepAgent } from "deepagents";
import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import { memoryEnabled, recallParticipant, rememberTurn, type Participant } from "./memory.ts";
import { normalizePhone, webSearchTool } from "./tools.ts";

// Live iMessage agent.
// The chat runtime is the Deep Agents harness. Its default tools are the
// in-memory filesystem (ls, read_file, write_file, edit_file, delete, glob, grep)
// and the task subagent. This process adds web_search. There is no shell.

const projectId = process.env.PROJECT_ID ?? process.env.SPECTRUM_PROJECT_ID;
const projectSecret = process.env.PROJECT_SECRET ?? process.env.SPECTRUM_PROJECT_SECRET;
const openRouterKey = process.env.OPENROUTER_API_KEY;
const model = process.env.OPENROUTER_MODEL || "openai/gpt-6-astra";
const memoryKey = process.env.SUPERMEMORY_API_KEY;

const agent = createDeepAgent({
  model: new ChatOpenRouter({ model }),
  checkpointer: new MemorySaver(),
  tools: [webSearchTool(model)],
  systemPrompt:
    "You are a helpful assistant texting over iMessage. Reply in plain conversational text. Keep answers short unless the person asks for detail. Use web_search for current public information. Scratch files exist only for this conversation and disappear when the process restarts. Do not mention internal tools, files, or planning.",
});

function safeError(error: unknown): string {
  let text = error instanceof Error ? error.message : "unknown error";
  for (const secret of [projectSecret, openRouterKey, memoryKey]) {
    if (secret) text = text.replaceAll(secret, "[redacted]");
  }
  return text;
}

function messageText(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object" && "text" in part && typeof part.text === "string") return part.text;
      return "";
    })
    .join("")
    .trim();
}

function replyText(messages: { content?: unknown; type?: string; getType?: () => string }[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message) continue;
    const kind = message.type ?? message.getType?.();
    if (kind && kind !== "ai") continue;
    const text = messageText(message.content);
    if (text) return text;
  }
  return "";
}

async function chat(sender: string, text: string): Promise<string> {
  const participant: Participant = { id: sender, label: sender };
  let memory = "";
  if (memoryEnabled()) {
    try {
      memory = await recallParticipant(participant, text);
    } catch (error) {
      console.error(`Memory recall failed for ${sender}: ${safeError(error)}`);
    }
  }
  const messages = memory
    ? [
        { role: "system", content: memory },
        { role: "user", content: text },
      ]
    : [{ role: "user", content: text }];
  const result = await agent.invoke(
    { messages },
    { configurable: { thread_id: sender }, recursionLimit: 40 },
  );
  const answer = replyText(result.messages);
  if (!answer) throw new Error("Deep agent returned an empty reply");
  if (memoryEnabled()) {
    try {
      await rememberTurn(participant, text, answer);
    } catch (error) {
      console.error(`Memory save failed for ${sender}: ${safeError(error)}`);
    }
  }
  return answer;
}

if (!projectId || !projectSecret || !openRouterKey) {
  console.error("Startup failed: PROJECT_ID, PROJECT_SECRET, and OPENROUTER_API_KEY must be set. Refusing to start.");
  process.exit(1);
}

const memoryBase = process.env.SUPERMEMORY_BASE_URL;
console.log(
  `Connecting iMessage agent to Photon project ${projectId} with Deep Agents on ${model}` +
    (memoryEnabled() ? `, memory at ${memoryBase}` : ", memory disabled"),
);

let app;
try {
  app = await Spectrum({
    projectId,
    projectSecret,
    providers: [imessage.config()],
  });
} catch (error) {
  console.error(`Photon startup failed: ${safeError(error)}`);
  process.exit(1);
}

console.log("Photon project connected. iMessage handler registered. Waiting for inbound messages.");

for await (const [space, message] of app.messages) {
  try {
    if (message.direction === "outbound") continue;

    const sender = normalizePhone(message.sender?.id ?? "");
    const platform = message.platform ?? "unknown";
    const content = message.content;

    try {
      await message.read();
      console.log(`Read receipt sent for ${sender}`);
    } catch (error) {
      console.error(`Read receipt failed for ${sender}: ${safeError(error)}`);
    }

    if (!content || content.type !== "text") {
      console.log(`Ignored ${platform} event from ${sender}: ${content?.type ?? "unknown"}`);
      continue;
    }

    const text = typeof content.text === "string" ? content.text : "";
    console.log(`Inbound ${platform} from ${sender} (${text.length} chars)`);

    try {
      await space.startTyping();
    } catch (error) {
      console.error(`Typing indicator failed for ${sender}: ${safeError(error)}`);
    }

    let answer: string;
    try {
      answer = await chat(sender, text);
    } catch (error) {
      console.error(`Chat failed for ${sender}: ${safeError(error)}`);
      answer = "I couldn't reach the model just now. Try again in a moment.";
    } finally {
      try {
        await space.stopTyping();
      } catch (error) {
        console.error(`Stop typing failed for ${sender}: ${safeError(error)}`);
      }
    }

    await space.send(answer);
    console.log(`Reply accepted for ${sender} (${answer.length} chars)`);
  } catch (error) {
    console.error(`Failed to handle inbound event: ${safeError(error)}`);
  }
}
