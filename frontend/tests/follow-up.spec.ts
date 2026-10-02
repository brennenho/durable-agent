import { expect, test } from "@playwright/test";
import type { ConversationDetail, SubmitFollowUpRequest } from "../src/lib/api";
import { conversationDetail, runDetail } from "./fixtures";

const first = { ...runDetail, status: "succeeded" as const, final_answer: "Created the document." };
const completed: ConversationDetail = { ...conversationDetail, status: "succeeded", runs: [first] };
const next = {
  ...runDetail,
  id: "00000000-0000-4000-8000-000000000003",
  prompt: "Add an introduction.",
};
const endpoint = `**/api/conversations/${completed.id}`;
const url = `/chats/${completed.id}`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [completed] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({
      json: { workspace_version_id: first.workspace_version_id, entries: [] },
    }),
  );
});

test("submits a reply once, keeps the earlier turn, and disables new work while active", async ({
  page,
}) => {
  let detail = completed;
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  const bodies: SubmitFollowUpRequest[] = [];
  await page.route(endpoint, (route) => route.fulfill({ json: detail }));
  await page.route(`${endpoint}/runs`, async (route) => {
    bodies.push(route.request().postDataJSON());
    await ready;
    detail = { ...completed, latest_run_id: next.id, status: "queued", runs: [first, next] };
    await route.fulfill({ json: next });
  });
  await page.goto(url);
  const form = page.getByRole("form", { name: "Reply" });
  await form.getByRole("textbox").fill(next.prompt);
  await form.getByRole("textbox").press("Enter");
  await expect(form.getByRole("button", { name: "Sending…" })).toBeDisabled();
  release();
  await expect(page.getByRole("article", { name: "Chat turn" })).toHaveCount(2);
  await expect(page.getByText(first.final_answer)).toBeVisible();
  await expect(form.getByRole("textbox")).toHaveValue("");
  await expect(form.getByRole("button", { name: "Send message" })).toBeDisabled();
  expect(bodies).toHaveLength(1);
  expect(bodies[0]).toMatchObject({ prompt: next.prompt, after_run_id: first.id });
  expect(bodies[0].submission_key).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page).toHaveURL(url);
});

test("retries an uncertain reply with the original key and predecessor after polling advances", async ({
  page,
}) => {
  let detail = completed;
  const bodies: SubmitFollowUpRequest[] = [];
  await page.route(endpoint, (route) => route.fulfill({ json: detail }));
  await page.route(`${endpoint}/runs`, async (route) => {
    bodies.push(route.request().postDataJSON());
    detail = {
      ...completed,
      latest_run_id: next.id,
      status: "running",
      runs: [first, { ...next, status: "running" }],
    };
    if (bodies.length === 1) await route.abort();
    else await route.fulfill({ json: next });
  });
  await page.goto(url);
  const form = page.getByRole("form", { name: "Reply" });
  await form.getByRole("textbox").fill(next.prompt);
  await form.getByRole("button", { name: "Send message" }).click();
  await expect(form.getByRole("alert")).toContainText("Retry to check the same message.");
  await expect(form.getByRole("textbox")).toHaveAttribute("readonly");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("article", { name: "Chat turn" })).toHaveCount(2);
  await form.getByRole("button", { name: "Try again" }).click();
  await expect(form.getByRole("textbox")).toHaveValue("");
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
});

for (const code of ["stale_conversation", "conversation_busy"]) {
  test(`preserves the draft after ${code} and uses a new key after refreshing`, async ({
    page,
  }) => {
    await page.clock.install();
    let detail = completed;
    let refreshBlocked = false;
    let release!: () => void;
    const refreshed = new Promise<void>((resolve) => {
      release = resolve;
    });
    const bodies: SubmitFollowUpRequest[] = [];
    await page.route(endpoint, async (route) => {
      if (refreshBlocked) await refreshed;
      await route.fulfill({ json: detail });
    });
    await page.route(`${endpoint}/runs`, async (route) => {
      bodies.push(route.request().postDataJSON());
      if (bodies.length === 1) {
        detail = {
          ...completed,
          latest_run_id: next.id,
          status: code === "conversation_busy" ? "running" : "succeeded",
          runs: [
            first,
            { ...next, status: code === "conversation_busy" ? "running" : "succeeded" },
          ],
        };
        refreshBlocked = true;
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code,
              message: "The conversation changed. Review the latest turn.",
              retryable: false,
            },
          },
        });
      }
      return route.fulfill({ json: next });
    });
    await page.goto(url);
    const form = page.getByRole("form", { name: "Reply" });
    await form.getByRole("textbox").fill("My unsent reply");
    await form.getByRole("button", { name: "Send message" }).click();
    await expect(form.getByRole("alert")).toContainText("The conversation changed.");
    await expect(form.getByRole("textbox")).toHaveValue("My unsent reply");
    await expect(form.getByRole("button", { name: "Try again" })).toBeDisabled();
    release();
    await expect(page.getByRole("article", { name: "Chat turn" })).toHaveCount(2);
    if (code === "conversation_busy") {
      await expect(form.getByRole("button", { name: "Try again" })).toBeDisabled();
      detail = { ...detail, status: "succeeded", runs: [first, { ...next, status: "succeeded" }] };
      await page.clock.fastForward(2_000);
    }
    await form.getByRole("button", { name: "Try again" }).click();
    await expect(form.getByRole("textbox")).toHaveValue("");
    expect(bodies[1].prompt).toBe(bodies[0].prompt);
    expect(bodies[1].after_run_id).toBe(next.id);
    expect(bodies[1].submission_key).not.toBe(bodies[0].submission_key);
  });
}

test("allows a reply after a failed turn without hiding the failure", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...completed,
        status: "failed",
        runs: [
          {
            ...first,
            status: "failed",
            final_answer: null,
            terminal_error: {
              code: "tool_failed",
              message: "Command deadline exceeded.",
              retryable: false,
            },
          },
        ],
      },
    }),
  );
  await page.goto(url);
  await expect(page.getByRole("alert", { name: "Run failure" })).toHaveText(
    "Command deadline exceeded.",
  );
  const form = page.getByRole("form", { name: "Reply" });
  await form.getByRole("textbox").fill("Try a smaller change.");
  await expect(form.getByRole("button", { name: "Send message" })).toBeEnabled();
});
