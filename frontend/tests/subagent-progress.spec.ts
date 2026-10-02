import { expect, test, type Page } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { conversationDetail } from "./fixtures";
import { delegation, parentRun } from "./subagent-fixtures";

async function setup(page: Page) {
  const state: { run: RunDetail } = { run: structuredClone(parentRun) };
  await page.clock.install();
  await page.route("**/api/conversations", (route) =>
    route.fulfill({
      json: [
        {
          ...conversationDetail,
          status: state.run.status,
          pause_requested: state.run.pause_requested,
        },
      ],
    }),
  );
  await page.route(`**/api/conversations/${conversationDetail.id}`, (route) =>
    route.fulfill({ json: { ...conversationDetail, status: state.run.status, runs: [state.run] } }),
  );
  await page.route(`**/api/runs/${parentRun.id}`, (route) => route.fulfill({ json: state.run }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({ json: { workspace_version_id: parentRun.workspace_version_id, entries: [] } }),
  );
  return state;
}

for (const width of [390, 1440]) {
  test(`shows live child progress without opening Activity at ${width}px`, async ({ page }) => {
    const state = await setup(page);
    state.run.children![0].status = "succeeded";
    state.run.children![1].status = "retrying";
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/chats/${conversationDetail.id}`);
    const status = page.getByRole("status", { name: "Execution status" });
    const children = page.getByRole("list", { name: "Subagents", exact: true });
    await expect(status).toHaveText("Waiting for subagents · 1/3 finished");
    await expect(children).toBeVisible();
    await page.screenshot({
      path: test.info().outputPath("subagent-progress.png"),
      fullPage: true,
    });
    await expect(children.getByRole("listitem").nth(1)).toContainText("Retrying");
    await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    await expect(page.getByLabel("1. Subagents", { exact: true })).toContainText(
      "Waiting for subagents",
    );
    await expect(children).toHaveCount(1);
    state.run.children![1].status = "failed";
    state.run.children![1].terminal_error = {
      code: "exhausted",
      message: "All retries used.",
      retryable: false,
    };
    await page.clock.fastForward(2000);
    await expect(status).toHaveText("Waiting for subagents · 2/3 finished");
    await expect(children).toContainText("All retries used.");
    state.run.children![2].status = "succeeded";
    state.run.status = "queued";
    state.run.operations[0] = { ...delegation, completed_at: "2026-10-01T12:00:20Z" };
    state.run.operations.push({
      ...delegation,
      id: "next-model",
      position: 1,
      kind: "model",
      tool_name: null,
    });
    await page.clock.fastForward(2000);
    await expect(status).toHaveText("Queued");
    await expect(page.getByRole("region", { name: "Subagent progress" })).toHaveCount(0);
    await expect(children).toBeVisible();
    await expect(children).toContainText("All retries used.");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}

test("group pause waits for the active child call, survives reload, and resumes", async ({
  page,
}) => {
  const state = await setup(page);
  state.run.children![0].status = "succeeded";
  state.run.children![1].status = "queued";
  await page.route(`**/api/runs/${parentRun.id}/pause`, (route) => {
    state.run.pause_requested = true;
    state.run
      .children!.filter((child) => child.status !== "succeeded")
      .forEach((child) => {
        child.pause_requested = true;
      });
    return route.fulfill({ json: state.run });
  });
  await page.route(`**/api/runs/${parentRun.id}/resume`, (route) => {
    state.run.pause_requested = false;
    state.run.children!.forEach((child) => {
      child.pause_requested = false;
    });
    return route.fulfill({ json: state.run });
  });
  await page.goto(`/chats/${conversationDetail.id}`);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const status = page.getByRole("status", { name: "Execution status" });
  await expect(status).toHaveText("Pausing… · 1/3 finished");
  await expect(
    page.getByRole("list", { name: "Subagents" }).getByRole("listitem").nth(1),
  ).toContainText("Paused");
  await page.clock.fastForward(2000);
  await expect(page.getByRole("img", { name: "Pause requested", exact: true })).toBeVisible();
  state.run.children![2].status = "queued";
  await page.clock.fastForward(2000);
  await expect(status).toHaveText("Paused · 1/3 finished");
  await page.reload();
  await expect(status).toHaveText("Paused · 1/3 finished");
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(status).toHaveText("Waiting for subagents · 1/3 finished");
});

test("delegate inspection does not poll Tools or invent a worker attempt", async ({ page }) => {
  await setup(page);
  let checkpoints = 0;
  await page.route("**/api/runs/*/operations/*/checkpoint", (route) => {
    checkpoints++;
    return route.fulfill({ json: null });
  });
  await page.goto(`/runs/${parentRun.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. Subagents", { exact: true }).locator(":scope > summary").click();
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByText("Control waits for every subagent to finish. No worker is held while waiting."),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: "Execution milestones" })).toContainText(
    "Operation planned",
  );
  await expect(page.getByRole("list", { name: "Execution milestones" })).not.toContainText(
    "Attempt",
  );
  await page.clock.fastForward(6000);
  expect(checkpoints).toBe(0);
  await expect(page.getByText(/saved by Tools/)).toHaveCount(0);
});
