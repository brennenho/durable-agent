import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { runDetail } from "./fixtures";

const started = runDetail.created_at;
const finished = "2026-10-01T12:00:02Z";
const model: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000010",
  position: 0,
  kind: "model",
  created_at: started,
  completed_at: finished,
  attempts: [
    {
      id: "00000000-0000-4000-8000-000000000020",
      attempt_no: 1,
      status: "unknown",
      started_at: started,
      finished_at: finished,
      error: null,
    },
    {
      id: "00000000-0000-4000-8000-000000000021",
      attempt_no: 2,
      status: "accepted",
      started_at: started,
      finished_at: finished,
      error: null,
      provider_request_id: "req_model",
      usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
  ],
  result: {
    kind: "model",
    output: {
      status: "incomplete",
      text: "I started editing.",
      tool_calls: [
        {
          name: "write_file",
          call_id: "call_write",
          arguments: { path: "report.md", content: "Draft" },
        },
      ],
    },
    native_context: [{ type: "reasoning", encrypted_content: "opaque-secret" }],
  },
};

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
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
});

test("separates saved model output from failed attempts and requested tool intent", async ({
  page,
}) => {
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        status: "failed",
        finished_at: finished,
        terminal_error: {
          code: "incomplete",
          message: "The model response was incomplete.",
          retryable: false,
        },
        operations: [model],
      },
    }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await history.getByLabel("1. Model call").locator(":scope > summary").click();
  await expect(history.getByText("Accepted from attempt 2.")).toBeHidden();
  await expect(history).toContainText("Response status: incomplete");
  await expect(history).toContainText("I started editing.");
  await expect(history.getByRole("heading", { name: "Requested tools (1)" })).toBeVisible();
  await expect(history.getByText("write_file · Call ID: call_write")).toBeHidden();
  await expect(history.getByText("Requests only;", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  const requestedInfo = history.getByRole("button", { name: "About requested tools" });
  await requestedInfo.hover();
  await expect(page.getByRole("tooltip")).toHaveText(
    "Tools requested by the model. Execution appears as separate steps.",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(history.getByText("File path", { exact: true })).toBeVisible();
  await expect(history.getByText("File contents", { exact: true })).toBeVisible();
  await history.getByRole("button", { name: "About file contents" }).focus();
  await expect(page.getByRole("tooltip")).toContainText("Text passed to write_file.");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(history).not.toContainText("Next: execute");
  await history.getByRole("tab", { name: "Technical details", exact: true }).click();
  await expect(history.getByText("Accepted from attempt 2.")).toBeVisible();
  await expect(history.getByText("write_file · Call ID: call_write")).toBeVisible();
  await history.getByText("Raw provider output", { exact: true }).click();
  await expect(history).toContainText("Encrypted context present");
  await expect(history).not.toContainText("opaque-secret");
  const attempts = history.locator(".attempt-list > li");
  await expect(attempts.nth(0)).toContainText("No saved model response for this attempt.");
  await expect(attempts.nth(0)).toContainText("Unavailable; this does not mean zero tokens.");
  await expect(attempts.nth(1)).toContainText("Duration: 2s");
  await expect(attempts.nth(1)).toContainText("Provider request ID: req_model");
  await expect(attempts.nth(1).locator("pre")).toContainText('"total_tokens": 15');
});

test("shows completed command failure with readable logs and its saved workspace", async ({
  page,
}) => {
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        operations: [
          {
            ...model,
            kind: "tool",
            tool_name: "run_command",
            attempts: [model.attempts[1]],
            result: {
              kind: "tool",
              call_id: "call_command",
              workspace_version_id: runDetail.workspace_version_id,
              output: {
                status: "ok",
                value: {
                  exit_code: 1,
                  stdout: "One test failed\nAssertionError",
                  stderr: "Warning: truncated",
                  truncated: true,
                },
              },
            },
          },
        ],
      },
    }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await history.getByLabel("1. run_command").locator(":scope > summary").click();
  await expect(history).toContainText("Exit code 1 · Command completed.");
  await expect(history.getByRole("heading", { name: "stdout", exact: true })).toBeVisible();
  await expect(history.locator("pre").first()).toHaveText("One test failed\nAssertionError");
  await expect(history).toContainText("Logs were truncated at the configured limit.");
  const workspace = history.getByText(`Saved workspace: ${runDetail.workspace_version_id}`, {
    exact: false,
  });
  await expect(workspace).toBeHidden();
  await history.getByRole("tab", { name: "Technical details", exact: true }).click();
  await expect(workspace).toBeVisible();
  await expect(history).not.toContainText("Retry policy");
});

test.describe("touch hints", () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

  test("opens on tap and dismisses without collapsing the step", async ({ page }) => {
    await page.route(`**/api/runs/${runDetail.id}`, (route) =>
      route.fulfill({ json: { ...runDetail, operations: [model] } }),
    );
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").tap();
    const step = page.getByLabel("1. Model call", { exact: true });
    await step.locator(":scope > summary").tap();
    const info = step.getByRole("button", { name: "About requested tools" });
    await info.tap();
    await expect(page.getByRole("tooltip")).toContainText("Execution appears as separate steps.");
    await info.tap();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await info.tap();
    await step.getByText("File path", { exact: true }).tap();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(step.getByText("Draft", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  });
});
