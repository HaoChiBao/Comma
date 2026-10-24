import { expect, test } from "@playwright/test";

const API = process.env.API_URL || "http://localhost:8000";
const REQUEST =
  "Build 100 complete desktop computers for our production run, within a $200,000 total component procurement budget, delivered by November 15.";

test("demo script runs in mock mode", async ({ page, request }) => {
  const reset = await request.post(`${API}/dev/reset`, { data: { include_demo_task: false } });
  expect(reset.ok()).toBeTruthy();

  await page.goto("/");
  await expect(page.getByTestId("mock-banner")).toContainText("no live iMessage");
  await page.getByTestId("login-phone").fill("+15555550100");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("composer-text")).toBeVisible();
  await page.getByTestId("composer-text").fill(REQUEST);
  await page.getByTestId("composer-send").click();

  await expect(page.getByTestId("task-status")).toHaveText("AWAITING_QUOTES", { timeout: 20000 });
  await expect(page.getByTestId("sandbox-inbox")).toContainText("RFQ-TOKEN:");
  await expect(page.getByTestId("rfq-list")).toContainText("RFQ");

  await page.getByTestId("btn-simulate-replies").click();
  await expect(page.getByTestId("task-status")).toHaveText("AWAITING_APPROVAL", { timeout: 20000 });
  await expect(page.getByTestId("plan-readiness")).toContainText("100 of 100");
  const total = await page.getByTestId("plan-total").innerText();
  expect(total).toMatch(/^\d+\.\d{2}$/);
  expect(Number(total)).toBeLessThan(200000);
  expect(total).not.toBe("192000.00");
  const versionBefore = await page.getByTestId("plan-version").innerText();

  await page.getByTestId("btn-disrupt-ram").click();
  await expect(page.getByTestId("task-status")).toHaveText("AWAITING_APPROVAL", { timeout: 20000 });
  await expect(page.getByTestId("plan-version")).not.toHaveText(versionBefore);
  await expect(page.getByTestId("plan-readiness")).toContainText("100 of 100");

  const approval = await page.getByTestId("approval-status").innerText();
  const token = approval.match(/APV-[A-F0-9]+/)?.[0];
  expect(token).toBeTruthy();
  await page.getByTestId("reply-text").fill(`APPROVE ${token}`);
  await page.getByTestId("reply-send").click();
  await expect(page.getByTestId("po-list")).toContainText("PO-", { timeout: 20000 });
  await expect(page.getByTestId("task-status")).toHaveText(/ORDERING|AWAITING_CONFIRMATION/);

  await page.getByTestId("btn-ack").click();
  await expect(page.getByTestId("task-status")).toHaveText("COMPLETED", { timeout: 20000 });
  await expect(page.getByTestId("completion-label")).toContainText("orders confirmed; delivery pending");
  await expect(page.getByTestId("audit-log")).toContainText("orders_confirmed");
  await expect(page.getByTestId("sandbox-inbox")).toContainText("sandbox");
});
