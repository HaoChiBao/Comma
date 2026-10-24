import { ChatOpenRouter } from "@langchain/openrouter";
import { MemorySaver } from "@langchain/langgraph";
import { createDeepAgent } from "deepagents";
import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import { memoryEnabled, recallOrganization, rememberTurn, type Organization } from "./memory.ts";
import { normalizePhone, procurementTools, senderContext, webSearchTool } from "./tools.ts";

// Live iMessage layer for the procurement agent.
// The chat runtime is the Deep Agents harness (filesystem, planning, subagents)
// with conversation state stored per sender. The filesystem is the in-memory
// StateBackend, so the agent does not get a shell on this host.
// Credentials come from the environment. Never log them.

const projectId = process.env.PROJECT_ID ?? process.env.SPECTRUM_PROJECT_ID;
const projectSecret = process.env.PROJECT_SECRET ?? process.env.SPECTRUM_PROJECT_SECRET;
const openRouterKey = process.env.OPENROUTER_API_KEY;
const model = process.env.OPENROUTER_MODEL || "openai/gpt-6-astra";
const procurementApiUrl = process.env.PROCUREMENT_API_URL || "https://api-production-69e9.up.railway.app";
const toolToken = process.env.TOOL_API_TOKEN;
const memoryKey = process.env.SUPERMEMORY_API_KEY;

const agent = createDeepAgent({
  model: new ChatOpenRouter({ model }),
  checkpointer: new MemorySaver(),
  tools: [...procurementTools(procurementApiUrl, toolToken ?? ""), webSearchTool(model)],
  systemPrompt:
    "You are a procurement assistant texting over iMessage for the sender's organization. Use list_tasks before task-specific tools. Use submit_message to start a request, add missing details, or pass APPROVE APV- and REJECT APV- decisions. Organization memory is shared by that organization and is background only. Bills of materials, compatibility, quotes, prices, stock, plans, approvals, and purchase orders come from the procurement tools. Use web_search only for public specs and manufacturers. Web prices are not supplier quotes. Supplier email stays in the sandbox and is not delivered. You cannot approve a plan yourself or create a purchase order. Reply in plain conversational text. Keep answers short unless the person asks for detail. Do not mention internal tools, files, or planning.",
});

function safeError(error: unknown): string {
  let text = error instanceof Error ? error.message : "unknown error";
  for (const secret of [projectSecret, openRouterKey, toolToken, memoryKey]) {
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

async function organizationFor(sender: string): Promise<Organization | null> {
  const response = await fetch(`${procurementApiUrl.replace(/\/$/, "")}/tools/identity`, {
    headers: {
      Authorization: `Bearer ${toolToken ?? ""}`,
      "X-Sender-Phone": sender,
    },
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status === 400 || response.status === 403) return null;
  if (!response.ok) throw new Error(`Identity lookup failed (${response.status})`);
  const body = (await response.json()) as {
    actor_id: string;
    display_name: string;
    role: string;
    company_id: string;
    company_name: string;
  };
  return {
    actorId: body.actor_id,
    displayName: body.display_name,
    role: body.role,
    companyId: body.company_id,
    companyName: body.company_name,
  };
}

async function chat(sender: string, text: string): Promise<string> {
  const organization = await organizationFor(sender);
  if (!organization) {
    return "This number is not a member of an organization, so I can't open procurement records or organization memory.";
  }
  let memory = "";
  if (memoryEnabled()) {
    try {
      memory = await recallOrganization(organization, text);
    } catch (error) {
      console.error(`Organization memory recall failed for ${organization.companyName}: ${safeError(error)}`);
    }
  }
  const messages = memory
    ? [
        { role: "system", content: memory },
        { role: "user", content: text },
      ]
    : [{ role: "user", content: text }];
  const result = await senderContext.run(sender, () =>
    agent.invoke(
      { messages },
      { configurable: { thread_id: `${organization.companyId}:${sender}` }, recursionLimit: 40 },
    ),
  );
  const answer = replyText(result.messages);
  if (!answer) throw new Error("Deep agent returned an empty reply");
  if (memoryEnabled()) {
    try {
      await rememberTurn(organization, text, answer);
    } catch (error) {
      console.error(`Organization memory save failed for ${organization.companyName}: ${safeError(error)}`);
    }
  }
  return answer;
}

if (!projectId || !projectSecret || !openRouterKey || !toolToken) {
  console.error("Startup failed: PROJECT_ID, PROJECT_SECRET, OPENROUTER_API_KEY, and TOOL_API_TOKEN must be set. Refusing to start.");
  process.exit(1);
}

const memoryBase = process.env.SUPERMEMORY_BASE_URL;
console.log(
  `Connecting iMessage agent to Photon project ${projectId} with Deep Agents on ${model}, procurement tools at ${procurementApiUrl}` +
    (memoryEnabled() ? `, organization memory at ${memoryBase}` : ", organization memory disabled"),
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
