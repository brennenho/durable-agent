import { expect, test } from "@playwright/test";
import type { ConversationDetail } from "../src/lib/api";
import { conversation, conversationDetail, runDetail } from "./fixtures";

const endpoint = `**/api/conversations/${conversation.id}`;
const url = `/chats/${conversation.id}`;
const completed: ConversationDetail = {
  ...conversationDetail,
  status: "succeeded",
  runs: [{ ...runDetail, status: "succeeded", final_answer: "Added the function and tests." }],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [conversation] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({
      json: { workspace_version_id: runDetail.workspace_version_id, entries: [] },
    }),
  );
});

test("renders ordered turns and refreshes an active reply until it completes", async ({ page }) => {
  await page.clock.install();
  const reply = {
    ...runDetail,
    id: "00000000-0000-4000-8000-000000000003",
    prompt: "Add a usage example.",
  };
  let detail: ConversationDetail = {
    ...conversationDetail,
    latest_run_id: reply.id,
    runs: [...completed.runs, reply],
  };
  let requests = 0;
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill({ json: detail });
  });
  await page.goto(url);
  const turns = page.getByRole("article", { name: "Chat turn" });
  await expect(turns).toHaveCount(2);
  await expect(turns.nth(0)).toContainText("Added the function and tests.");
  await expect(turns.nth(1)).toContainText(reply.prompt);
  detail = {
    ...detail,
    status: "succeeded",
    runs: [
      ...completed.runs,
      { ...reply, status: "succeeded", final_answer: "Added the example." },
    ],
  };
  await page.clock.fastForward(2_000);
  await expect(turns.nth(1)).toContainText("Added the example.");
  const count = requests;
  await page.clock.fastForward(10_000);
  expect(requests).toBe(count);
});

test("preserves saved turns on refresh failure and recovers on focus", async ({ page }) => {
  let failing = false;
  let requests = 0;
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill(
      failing
        ? {
            status: 503,
            json: { error: { code: "unavailable", message: "Chat unavailable.", retryable: true } },
          }
        : { json: completed },
    );
  });
  await page.goto(url);
  await expect(page.getByText("Added the function and tests.")).toBeVisible();
  failing = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("alert", { name: "Chat update error" })).toContainText(
    "Chat unavailable.",
  );
  await expect(page.getByText("Added the function and tests.")).toBeVisible();
  failing = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("alert", { name: "Chat update error" })).toHaveCount(0);
  expect(requests).toBeGreaterThanOrEqual(3);
});

test("stops polling and removes the focus listener when leaving a chat", async ({ page }) => {
  await page.clock.install();
  let requests = 0;
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill({ json: conversationDetail });
  });
  await page.goto(url);
  await expect(page.getByRole("article", { name: "Chat turn" })).toBeVisible();
  await page.getByRole("link", { name: "New chat" }).click();
  await expect(page).toHaveURL("/");
  const count = requests;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.fastForward(10_000);
  expect(requests).toBe(count);
});

test("shows a missing conversation without repeated polling", async ({ page }) => {
  await page.clock.install();
  let requests = 0;
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill({
      status: 404,
      json: { error: { code: "not_found", message: "Chat not found.", retryable: false } },
    });
  });
  await page.goto(url);
  await expect(page.getByRole("alert", { name: "Chat update error" })).toHaveText(
    "Chat not found.",
  );
  const count = requests;
  await page.clock.fastForward(10_000);
  expect(requests).toBe(count);
});
