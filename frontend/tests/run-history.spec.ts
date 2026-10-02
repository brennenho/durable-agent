import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { runDetail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/runs/*/operations/*", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          code: "inspection_unavailable",
          message: "Inspection unavailable",
          retryable: true,
        },
      },
    }),
  );
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({
      json: {
        workspace_version_id: runDetail.workspace_version_id,
        entries: [],
      },
    }),
  );
});

const started = "2026-10-01T12:00:00Z";
const finished = "2026-10-01T12:01:00Z";
const detail: RunDetail = {
  ...runDetail,
  status: "succeeded",
  finished_at: finished,
  next_attempt_at: null,
  operations: [
    {
      id: "00000000-0000-4000-8000-000000000010",
      position: 0,
      kind: "tool",
      created_at: started,
      completed_at: finished,
      result: {
        kind: "tool",
        call_id: "call_1",
        workspace_version_id: runDetail.workspace_version_id,
        output: {
          status: "ok",
          value: { exit_code: 1, stdout: "1 test failed", stderr: "", truncated: false },
        },
      },
      attempts: [
        {
          id: "00000000-0000-4000-8000-000000000011",
          attempt_no: 1,
          status: "error",
          started_at: started,
          finished_at: finished,
          error: { code: "sandbox_crash", message: "The sandbox stopped.", retryable: true },
        },
        {
          id: "00000000-0000-4000-8000-000000000012",
          attempt_no: 2,
          status: "unknown",
          started_at: started,
          finished_at: finished,
          error: null,
        },
        {
          id: "00000000-0000-4000-8000-000000000013",
          attempt_no: 3,
          status: "accepted",
          started_at: started,
          finished_at: finished,
          error: null,
        },
      ],
    },
  ],
};

test("shows earlier failures, unknown outcomes, and accepted tool output", async ({ page }) => {
  await page.route(`**/api/runs/${detail.id}`, (route) => route.fulfill({ json: detail }));
  await page.goto(`/runs/${detail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history.getByLabel("1. Tool call", { exact: true })).toBeVisible();
  await expect(history.getByText("Worker lease expired; outcome unknown. Retried.")).toBeVisible();
  await history.getByLabel("1. Tool call").locator(":scope > summary").click();
  await expect(history.getByText("1 test failed", { exact: true })).toBeVisible();
  await expect(history.getByText("sandbox_crash: The sandbox stopped.")).toBeHidden();
  await history.getByRole("tab", { name: "Technical details", exact: true }).click();
  await expect(history.locator(".attempt-list > li")).toHaveCount(3);
  await expect(history.locator(".attempt-list > li").nth(0)).toContainText(
    "sandbox_crash: The sandbox stopped.",
  );
  await expect(history.locator(".attempt-list > li").nth(1)).toContainText(
    "No conclusive result was recorded.",
  );
  await expect(history.locator(".attempt-list > li").nth(2)).toContainText("Attempt 3 · accepted");
  await expect(history.getByRole("alert", { name: "Input inspection error" })).toHaveText(
    "Inspection unavailable",
  );
  await history.getByRole("tab", { name: "Output", exact: true }).click();
  await expect(history.getByText("1 test failed", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Completed");
});

test("shows an empty history and a planned operation awaiting a worker", async ({ page }) => {
  let current = runDetail;
  await page.clock.install();
  await page.route(`**/api/runs/${detail.id}`, (route) => route.fulfill({ json: current }));
  await page.goto(`/runs/${detail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history).toContainText("No operations recorded yet.");
  current = {
    ...runDetail,
    operations: [
      { ...detail.operations[0], kind: "model", result: null, completed_at: null, attempts: [] },
    ],
  };
  await page.clock.fastForward(2_000);
  await expect(history.getByLabel("1. Model call", { exact: true })).toBeVisible();
  await expect(history.getByText("Queued", { exact: true })).toBeVisible();
  await expect(history.getByText("Saved tool output")).toHaveCount(0);
});

test("shows unfinished work as stopped when the run has failed", async ({ page }) => {
  const failed: RunDetail = {
    ...detail,
    status: "failed",
    terminal_error: {
      code: "attempts_exhausted",
      message: "All attempts failed.",
      retryable: false,
    },
    operations: [{ ...detail.operations[0], completed_at: null, result: null, attempts: [] }],
  };
  await page.route(`**/api/runs/${detail.id}`, (route) => route.fulfill({ json: failed }));
  await page.goto(`/runs/${detail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history.getByText("Stopped", { exact: true })).toBeVisible();
  await expect(history.getByText("Pending", { exact: true })).toHaveCount(0);
  await expect(history.getByText("Waiting for a worker.")).toHaveCount(0);
});

for (const width of [390, 1440]) {
  test(`keeps long command rows readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const command = "python -m pytest tests/calculator/test_division.py --verbose --tb=short";
    await page.route(`**/api/runs/${detail.id}`, (route) =>
      route.fulfill({
        json: {
          ...detail,
          operations: [{ ...detail.operations[0], tool_name: "run_command", tool_target: command }],
        },
      }),
    );
    await page.goto(`/runs/${detail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const step = page.getByLabel("1. run_command", { exact: true });
    await expect(step.getByText("Completed", { exact: true })).toBeVisible();
    await expect(step.getByTitle(command)).toBeVisible();
    await step.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    await expect(step.getByText("1 test failed", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  });
}
