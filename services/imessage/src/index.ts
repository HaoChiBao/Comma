import { ChatOpenRouter } from "@langchain/openrouter";
import { MemorySaver } from "@langchain/langgraph";
import { createDeepAgent } from "deepagents";
import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";

// Live iMessage layer for the procurement agent.
// The chat runtime is the Deep Agents harness (filesystem, planning, subagents)
// with conversation state stored per sender. The filesystem is the in-memory
// StateBackend, so the agent does not get a shell on this host.
// Credentials come from the environment. Never log them.

const projectId = process.env.PROJECT_ID ?? process.env.SPECTRUM_PROJECT_ID;
const projectSecret = process.env.PROJECT_SECRET ?? process.env.SPECTRUM_PROJECT_SECRET;
const openRouterKey = process.env.OPENROUTER_API_KEY;
const model = process.env.OPENROUTER_MODEL || "openai/gpt-6-astra";

const agent = createDeepAgent({
  model: new ChatOpenRouter({ model }),
  checkpointer: new MemorySaver(),
  systemPrompt:
    "You are a helpful assistant texting over iMessage. Reply in plain conversational text. Keep answers short unless the person asks for detail. Do not mention internal tools, files, or planning.",
});

function safeError(error: unknown): string {
  let text = error instanceof Error ? error.message : "unknown error";
  for (const secret of [projectSecret, openRouterKey]) {
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
  const result = await agent.invoke(
    { messages: [{ role: "user", content: text }] },
    { configurable: { thread_id: sender }, recursionLimit: 40 },
  );
  const answer = replyText(result.messages);
  if (!answer) throw new Error("Deep agent returned an empty reply");
  return answer;
}

if (!projectId || !projectSecret || !openRouterKey) {
  console.error("Startup failed: PROJECT_ID, PROJECT_SECRET, and OPENROUTER_API_KEY must be set. Refusing to start.");
  process.exit(1);
}

console.log(`Connecting iMessage agent to Photon project ${projectId} with Deep Agents on ${model}`);

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

    const sender = message.sender?.id ?? "unknown";
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
