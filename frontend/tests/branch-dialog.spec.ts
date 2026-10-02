import { expect, test, type Page } from "@playwright/test";
import type { ConversationDetail, RunDetail, SubmitForkRequest } from "../src/lib/api";
import { conversation, runDetail } from "./fixtures";

const version = "00000000-0000-4000-8000-000000000030";
const operation: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000011",
  position: 0,
  kind: "tool",
  tool_name: "write_file",
  tool_target: "checkpoint.txt",
  created_at: runDetail.created_at,
  completed_at: "2026-10-01T12:00:01Z",
  checkpoint: { workspace_version_id: version },
  attempts: [],
  result: {
    kind: "tool",
    call_id: "save",
    workspace_version_id: version,
    output: { status: "ok", value: { content: "Saved at the checkpoint" } },
  },
};
const source: RunDetail = {
  ...runDetail,
  operations: [
    operation,
    { ...operation, id: "00000000-0000-4000-8000-000000000012", position: 1, checkpoint: null },
  ],
};
const endpoint = `**/api/runs/${source.id}`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route(endpoint, (route) => route.fulfill({ json: source }));
  await page.route("**/api/runs/*/files?**", (route) => {
    const selected = new URL(route.request().url()).searchParams.get("version_id");
    return route.fulfill({
      json: {
        workspace_version_id: selected,
        entries:
          selected === version ? [{ path: "checkpoint.txt", kind: "file", size_bytes: 10 }] : [],
      },
    });
  });
});

async function openBranch(page: Page) {
  await page.goto(`/runs/${source.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. write_file", { exact: true }).locator(":scope > summary").click();
  await page.getByRole("button", { name: "Branch from here" }).click();
  return page.getByRole("dialog", { name: "Branch from step 1" });
}

for (const width of [390, 1440]) {
  test(`opens eligible checkpoints with exact files and keyboard controls at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    let submissions = 0;
    await page.route(`${endpoint}/forks`, (route) => {
      submissions++;
      return route.abort();
    });
    await page.route(`${endpoint}/file?**`, (route) => {
      expect(new URL(route.request().url()).searchParams.get("version_id")).toBe(version);
      return route.fulfill({ body: "The earlier file" });
    });
    const dialog = await openBranch(page);
    await expect(dialog.getByRole("textbox", { name: "New instruction" })).toBeFocused();
    await expect(dialog.getByRole("button", { name: "Create branch" })).toBeDisabled();
    await expect(dialog).toContainText("Files at step 1");
    await dialog.getByRole("button", { name: "Preview checkpoint.txt" }).click();
    await expect(page.getByRole("dialog", { name: "checkpoint.txt" })).toContainText(
      "The earlier file",
    );
    await page.keyboard.press("Escape");
    await expect(dialog.getByRole("button", { name: "Preview checkpoint.txt" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await dialog.getByRole("textbox").fill("Do not submit this draft");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("button", { name: "Branch from here" })).toBeFocused();
    expect(submissions).toBe(0);
    await page.getByLabel("2. write_file", { exact: true }).locator(":scope > summary").click();
    await expect(page.getByRole("button", { name: "Branch from here" })).toHaveCount(1);
  });
}

test("creates a new conversation without replaying source steps in the chat", async ({ page }) => {
  let body: SubmitForkRequest | undefined;
  const summary = {
    ...conversation,
    title: "Try a smaller change.",
    latest_run_id: conversation.id,
  };
  const branch: ConversationDetail = {
    ...summary,
    runs: [{ ...runDetail, id: summary.latest_run_id, prompt: summary.title }],
    forked_from: {
      conversation_id: source.id,
      run_id: source.id,
      operation_id: operation.id,
      position: operation.position,
    },
  };
  await page.route(`${endpoint}/forks`, (route) => {
    body = route.request().postDataJSON();
    return route.fulfill({ json: summary });
  });
  await page.route(`**/api/conversations/${branch.id}`, (route) => route.fulfill({ json: branch }));
  const dialog = await openBranch(page);
  await dialog.getByRole("textbox").fill(branch.runs[0].prompt);
  await dialog.getByRole("button", { name: "Create branch" }).click();
  await expect(page).toHaveURL(`/chats/${branch.id}`);
  expect(body).toMatchObject({ prompt: branch.runs[0].prompt, after_operation_id: operation.id });
  expect(body?.submission_key).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page.getByRole("article", { name: "Chat turn" })).toHaveCount(1);
  await expect(page.getByText("Saved at the checkpoint", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Branched from step 1 in the source chat" }),
  ).toHaveAttribute("href", `/chats/${source.id}`);
});

test("retries the same creation after a lost response and uses a new key for an edited instruction", async ({
  page,
}) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...source,
        operations: source.operations.map((item) => ({
          ...item,
          checkpoint: operation.checkpoint,
        })),
      },
    }),
  );
  const bodies: SubmitForkRequest[] = [];
  await page.route(`${endpoint}/forks`, (route) => {
    bodies.push(route.request().postDataJSON());
    return route.abort();
  });
  const dialog = await openBranch(page);
  await dialog.getByRole("textbox").fill("First approach");
  await dialog.getByRole("button", { name: "Create branch" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Retry to open the same branch.");
  await dialog.getByRole("button", { name: "Close branch dialog" }).click();
  await page.getByRole("button", { name: "Branch from here" }).click();
  await dialog.getByRole("button", { name: "Retry creation" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect(bodies[1]).toEqual(bodies[0]);
  await dialog.getByRole("textbox").fill("Different approach");
  await dialog.getByRole("button", { name: "Create branch" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect(bodies[2].submission_key).not.toBe(bodies[0].submission_key);
  await dialog.getByRole("button", { name: "Close branch dialog" }).click();
  const second = page.getByLabel("2. write_file", { exact: true });
  await second.locator(":scope > summary").click();
  await second.getByRole("button", { name: "Branch from here" }).click();
  const next = page.getByRole("dialog", { name: "Branch from step 2" });
  await next.getByRole("textbox").fill("Different approach");
  await next.getByRole("button", { name: "Create branch" }).click();
  await expect(next.getByRole("alert")).toBeVisible();
  expect(bodies[3].after_operation_id).toBe(source.operations[1].id);
  expect(bodies[3].submission_key).not.toBe(bodies[2].submission_key);
});

test("closing a pending creation cancels the request but preserves its retry key", async ({
  page,
}) => {
  const bodies: SubmitForkRequest[] = [];
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`${endpoint}/forks`, async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) await waiting;
    await route.fulfill({
      status: 409,
      json: {
        error: { code: "invalid_checkpoint", message: "Checkpoint unavailable.", retryable: false },
      },
    });
  });
  const dialog = await openBranch(page);
  await dialog.getByRole("textbox").fill("New approach");
  await dialog.getByRole("button", { name: "Create branch" }).click();
  await expect(dialog.getByRole("button", { name: "Creating…" })).toBeDisabled();
  const cancelled = page.waitForEvent("requestfailed", (request) =>
    request.url().endsWith("/forks"),
  );
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await cancelled;
  release();
  await page.getByRole("button", { name: "Branch from here" }).click();
  await dialog.getByRole("button", { name: "Retry creation" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Checkpoint unavailable.");
  expect(bodies[1]).toEqual(bodies[0]);
});
