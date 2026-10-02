import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { runDetail } from "./fixtures";

const started = "2026-10-01T12:00:00Z";
const model: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000010",
  position: 0,
  kind: "model",
  created_at: started,
  completed_at: started,
  attempts: [],
  result: {
    kind: "model",
    native_context: [],
    output: {
      status: "completed",
      text: "",
      tool_calls: [
        { call_id: "one", name: "write_file", arguments: { path: "report.md", content: "Hi" } },
        { call_id: "two", name: "run_command", arguments: { command: "python -m pytest -q" } },
      ],
    },
  },
};
const tool: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000011",
  position: 1,
  kind: "tool",
  tool_name: "write_file",
  tool_target: "report.md",
  created_at: started,
  completed_at: null,
  result: null,
  attempts: [],
};

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-01T12:00:12Z") });
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
});

test("shows the loop position, named tool, and elapsed execution", async ({ page }) => {
  let current: RunDetail = { ...runDetail, operations: [model, tool] };
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: current }));
  await page.goto(`/runs/${runDetail.id}`);
  await expect(page.getByRole("status")).toHaveText(
    "Model round 1 · Tool 1 of 2 · Waiting for a worker: write_file",
  );
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history.getByRole("heading", { name: "2. write_file" })).toBeVisible();
  await expect(history.getByText("report.md", { exact: true })).toBeVisible();
  current = {
    ...current,
    status: "running",
    next_attempt_at: null,
    operations: [
      model,
      {
        ...tool,
        attempts: [
          {
            id: "00000000-0000-4000-8000-000000000012",
            attempt_no: 1,
            status: "running",
            started_at: started,
            finished_at: null,
            error: null,
          },
        ],
      },
    ],
  };
  await page.clock.fastForward(2000);
  await expect(page.getByRole("status")).toContainText("Running tool: write_file · 14s elapsed");
  await expect(history.locator('[aria-current="step"]')).toHaveCount(1);
});

test("explains retry eligibility and unknown outcomes without claiming a sandbox crash", async ({
  page,
}) => {
  const current: RunDetail = {
    ...runDetail,
    status: "retrying",
    max_attempts: 5,
    next_attempt_at: "2026-10-01T12:00:20Z",
    operations: [
      model,
      {
        ...tool,
        attempts: [
          {
            id: "00000000-0000-4000-8000-000000000012",
            attempt_no: 1,
            status: "unknown",
            started_at: started,
            finished_at: "2026-10-01T12:00:10Z",
            error: null,
          },
        ],
      },
    ],
  };
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: current }));
  await page.goto(`/runs/${runDetail.id}`);
  await expect(page.getByRole("status")).toContainText("Waiting to retry");
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history).toContainText("Attempt 1 of 5 · unknown");
  await expect(history).toContainText("No conclusive result was recorded.");
  await expect(history).toContainText("starts when a worker claims it.");
  await expect(history).not.toContainText("sandbox crash");
  await page.clock.fastForward(9000);
  await expect(page.getByRole("status")).toContainText("Waiting for a worker to retry");
});

test("a failed command is a completed result and the next model call waits for a worker", async ({
  page,
}) => {
  const current: RunDetail = {
    ...runDetail,
    operations: [
      model,
      {
        ...tool,
        tool_name: "run_command",
        completed_at: started,
        result: {
          kind: "tool",
          call_id: "one",
          workspace_version_id: runDetail.workspace_version_id,
          output: {
            status: "ok",
            value: { exit_code: 1, stdout: "1 failed", stderr: "", truncated: false },
          },
        },
      },
      {
        ...model,
        id: "00000000-0000-4000-8000-000000000013",
        position: 2,
        completed_at: null,
        result: null,
      },
    ],
  };
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: current }));
  await page.goto(`/runs/${runDetail.id}`);
  await expect(page.getByRole("status")).toHaveText("Model round 2 · Waiting for a worker");
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  const history = page.getByRole("region", { name: "Execution history" });
  await expect(history).toContainText(
    "Next: request a model response with the saved tool results.",
  );
  await expect(history).not.toContainText("Retry pending");
});
