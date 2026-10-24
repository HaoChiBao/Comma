import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";

// Live iMessage layer for the procurement agent.
// Stable Spectrum docs: https://photon.codes/docs/spectrum-ts
// Credentials come from the environment (PROJECT_ID / PROJECT_SECRET). Never log them.

const projectId = process.env.PROJECT_ID ?? process.env.SPECTRUM_PROJECT_ID;
const projectSecret = process.env.PROJECT_SECRET ?? process.env.SPECTRUM_PROJECT_SECRET;

function safeError(error: unknown): string {
  const text = error instanceof Error ? error.message : "unknown error";
  if (projectSecret && text.includes(projectSecret)) {
    return text.replaceAll(projectSecret, "[redacted]");
  }
  return text;
}

if (!projectId || !projectSecret) {
  console.error("Startup failed: PROJECT_ID and PROJECT_SECRET must be set. Refusing to start.");
  process.exit(1);
}

console.log(`Connecting iMessage agent to Photon project ${projectId}`);

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

    if (!content || content.type !== "text") {
      console.log(`Ignored ${platform} event from ${sender}: ${content?.type ?? "unknown"}`);
      continue;
    }

    const text = typeof content.text === "string" ? content.text : "";
    console.log(`Inbound ${platform} from ${sender} (${text.length} chars)`);

    await space.send(`Photon Spectrum is connected. You said: ${text}`);
    console.log(`Reply accepted for ${sender}`);
  } catch (error) {
    console.error(`Failed to handle inbound event: ${safeError(error)}`);
  }
}
