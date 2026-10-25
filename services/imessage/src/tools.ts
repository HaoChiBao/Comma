import { ChatOpenRouter } from "@langchain/openrouter";
import { tool } from "langchain";
import { z } from "zod";

export function normalizePhone(value: string): string {
  let text = value.trim().replaceAll(" ", "").replaceAll("-", "");
  if (text.startsWith("00")) text = `+${text.slice(2)}`;
  if (/^\d+$/.test(text)) text = `+${text}`;
  return text;
}

export function webSearchTool(model: string) {
  return tool(
    async ({ query }) => {
      const searcher = new ChatOpenRouter({
        model,
        plugins: [{ id: "web", max_results: 5 }],
        maxTokens: 700,
      });
      const message = await searcher.invoke([
        {
          role: "system",
          content:
            "Search the public web and reply with a JSON array of up to 5 objects with title, url, and snippet.",
        },
        { role: "user", content: query },
      ]);
      const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content);
      return content.slice(0, 4_000);
    },
    {
      name: "web_search",
      description: "Search the public web. Use this for current public information that is not already in the conversation.",
      schema: z.object({
        query: z.string().describe("What to look up"),
      }),
    },
  );
}
