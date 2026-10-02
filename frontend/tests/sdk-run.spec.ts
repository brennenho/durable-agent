import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { runDetail } from "./fixtures";

const sdkRun: RunDetail = {
  ...runDetail,
  agent: { name: "reviewer", version: "1", app_ref: "example.app:app", build_id: "build-1" },
  status: "succeeded",
  finished_at: "2026-10-01T12:01:00Z",
  next_attempt_at: null,
  final_answer: "Legacy answer should not appear.",
  output: null,
};
const endpoint = `**/api/runs/${sdkRun.id}`;
const url = `/runs/${sdkRun.id}`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({ json: { workspace_version_id: sdkRun.workspace_version_id, entries: [] } }),
  );
});

for (const output of [null, false, 0, "", "  ", [], {}, { checks: ["passed"], count: 3 }]) {
  test(`renders SDK output ${JSON.stringify(output)} without falling back to final_answer`, async ({
    page,
  }) => {
    await page.route(endpoint, (route) => route.fulfill({ json: { ...sdkRun, output } }));
    await page.goto(url);
    const answer = page.getByRole("region", { name: "Final output" });
    await expect(answer.locator("pre")).toHaveText(JSON.stringify(output, null, 2));
    await expect(answer).not.toContainText(sdkRun.final_answer!);
    await expect(page.getByLabel("Activity", { exact: true })).not.toHaveAttribute("open");
  });
}

test("renders SDK string output as safe Markdown", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...sdkRun,
        output: "## Review\n\n**Passed**\n\n<script>window.injected = true</script>",
      },
    }),
  );
  await page.goto(url);
  const answer = page.getByRole("region", { name: "Final output" });
  await expect(answer.getByRole("heading", { name: "Review" })).toBeVisible();
  await expect(answer.locator("strong")).toHaveText("Passed");
  await expect(answer).toContainText("<script>window.injected = true</script>");
  await expect(answer.locator("script")).toHaveCount(0);
});

test("keeps legacy Markdown answers when agent identity is absent", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: { ...sdkRun, agent: null, output: { unused: true }, final_answer: "**Done**" },
    }),
  );
  await page.goto(url);
  const answer = page.getByRole("region", { name: "Final output" });
  await expect(answer.locator("strong")).toHaveText("Done");
  await expect(answer.locator("pre")).toHaveCount(0);
});

test("waits for SDK success and stops polling after a JSON-null result", async ({ page }) => {
  let current: RunDetail = { ...sdkRun, status: "running", finished_at: null };
  let requests = 0;
  await page.clock.install();
  await page.route(endpoint, (route) => {
    requests++;
    return route.fulfill({ json: current });
  });
  await page.goto(url);
  await expect(page.getByRole("status", { name: "Execution status" })).toHaveText("Running");
  await page.clock.pauseAt(new Date());
  await expect(page.getByRole("region", { name: "Final output" })).toHaveCount(0);
  current = sdkRun;
  await page.clock.fastForward(2_000);
  await expect(page.getByRole("region", { name: "Final output" })).toHaveText("null");
  const completedCount = requests;
  await page.clock.fastForward(10_000);
  expect(requests).toBe(completedCount);
});

test("shows SDK failure without presenting an output as success", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...sdkRun,
        status: "failed",
        terminal_error: { code: "invalid_output", message: "Review failed.", retryable: false },
      },
    }),
  );
  await page.goto(url);
  await expect(page.getByRole("alert", { name: "Run failure" })).toHaveText("Review failed.");
  await expect(page.getByRole("region", { name: "Final output" })).toHaveCount(0);
});

test("distinguishes missing SDK output from JSON null", async ({ page }) => {
  await page.route(endpoint, (route) => route.fulfill({ json: { ...sdkRun, output: undefined } }));
  await page.goto(url);
  await expect(page.getByRole("region", { name: "Final output" })).toHaveText(
    "The saved output is unavailable.",
  );
});

for (const width of [320, 1440]) {
  test(`keeps nested SDK JSON readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const output = { files: [{ path: `src/${"nested/".repeat(30)}review.py`, passed: true }] };
    await page.route(endpoint, (route) => route.fulfill({ json: { ...sdkRun, output } }));
    await page.goto(url);
    await expect(page.getByRole("region", { name: "Final output" }).locator("pre")).toHaveText(
      JSON.stringify(output, null, 2),
    );
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: test.info().outputPath("sdk-output.png"), fullPage: true });
  });
}
