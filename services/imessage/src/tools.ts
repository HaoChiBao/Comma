import { tool } from "langchain";
import { z } from "zod";

const taskId = z.object({
  task_id: z.string().describe("Procurement task id from list_tasks"),
});

const catalog: { name: string; description: string; schema: z.ZodObject }[] = [
  {
    name: "list_tasks",
    description: "List Acme Computing procurement tasks. Returns id, public_code, status, and quantity. Call this first when you do not already have a task id.",
    schema: z.object({}),
  },
  {
    name: "get_task",
    description: "Get one Acme procurement task, including status, quantity, budget, deadline, and destination.",
    schema: taskId,
  },
  {
    name: "list_known_suppliers",
    description: "List approved suppliers that can quote this task, with their ids and SKUs.",
    schema: taskId,
  },
  {
    name: "get_supplier_catalog",
    description: "List catalog rows that match this task's bill of materials.",
    schema: taskId,
  },
  {
    name: "get_valid_quotes",
    description: "List fresh supplier quotes for this task and any RFQs still needed.",
    schema: taskId,
  },
  {
    name: "send_rfq",
    description: "Queue a request for quote to one supplier. Email stays in the sandbox and is not delivered.",
    schema: taskId.extend({
      supplier_id: z.string().describe("Supplier id from list_known_suppliers"),
    }),
  },
  {
    name: "list_pending_rfqs",
    description: "List RFQs for this task that do not have a quote yet.",
    schema: taskId,
  },
  {
    name: "request_supplier_clarification",
    description: "Queue a clarification question on an existing RFQ. It stays in the sandbox.",
    schema: z.object({
      rfq_id: z.string(),
      question: z.string().optional(),
    }),
  },
  {
    name: "calculate_purchase_plan",
    description: "Compute the landed-cost purchase plan for this task. Does not place an order.",
    schema: taskId,
  },
  {
    name: "propose_plan",
    description: "Save the current feasible plan as the proposed plan. Does not approve it or create a purchase order.",
    schema: taskId,
  },
  {
    name: "request_approval",
    description: "Ask a human to approve a proposed plan. Does not approve it.",
    schema: taskId.extend({
      plan_id: z.string().describe("Plan id returned by propose_plan"),
    }),
  },
  {
    name: "get_task_events",
    description: "List the audit events for one task.",
    schema: taskId,
  },
];

export function procurementTools(apiUrl: string, token: string) {
  const base = apiUrl.replace(/\/$/, "");
  return catalog.map((spec) =>
    tool(
      async (input) => {
        const response = await fetch(`${base}/tools/${spec.name}`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(input ?? {}),
          signal: AbortSignal.timeout(60_000),
        });
        const text = await response.text();
        if (!response.ok) {
          return `Tool ${spec.name} failed (${response.status}): ${text.slice(0, 500)}`;
        }
        return text.slice(0, 12_000);
      },
      {
        name: spec.name,
        description: spec.description,
        schema: spec.schema,
      },
    ),
  );
}
