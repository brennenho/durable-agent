import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import type { CrashSnapshot } from "../src/lib/crash-test";
import { operationEvents } from "../src/lib/execution-events";
import { runDetail } from "./fixtures";

const at = (seconds: string) => `2026-10-01T12:00:${seconds}Z`;
const delegation: RunDetail["operations"][number] = {
  id: "delegation",
  position: 0,
  kind: "tool",
  tool_name: "delegate",
  created_at: at("00"),
  completed_at: null,
  result: null,
  attempts: [],
};
const child: RunDetail = {
  ...runDetail,
  id: "child",
  status: "retrying",
  parent: {
    run_id: runDetail.id,
    conversation_id: runDetail.id,
    operation_id: delegation.id,
    name: "Review tests",
  },
  operations: [
    {
      ...delegation,
      id: "child-edit",
      tool_name: "write_file",
      created_at: at("01"),
      attempts: [
        {
          id: "attempt",
          attempt_no: 1,
          status: "unknown",
          started_at: at("02"),
          finished_at: at("05"),
          error: null,
        },
      ],
    },
  ],
};
const parent: RunDetail = {
  ...runDetail,
  status: "waiting",
  operations: [delegation],
  children: [
    { ...child, name: "Review tests", parent_operation_id: delegation.id, child_index: 0 },
  ],
};

test("delegation milestones describe child outcomes without inventing Tools publication", () => {
  const error = { code: "failed", message: "Child ran out of retries.", retryable: false };
  const events = operationEvents(
    {
      ...delegation,
      completed_at: at("10"),
      result: {
        kind: "tool",
        call_id: "delegate",
        output: { status: "ok", value: {} },
        workspace_version_id: runDetail.workspace_version_id,
      },
    },
    { completed_at: at("08"), workspace_version_id: "must-not-appear" },
    [{ ...parent.children![0], status: "failed", finished_at: at("09"), terminal_error: error }],
  );
  expect(events.map((event) => event.label)).toEqual([
    "Operation planned",
    "Subagent created",
    "Subagent failed",
    "Subagent reports accepted by Control",
  ]);
  expect(events[2]).toMatchObject({
    runId: "child",
    runName: "Review tests",
    failed: true,
    detail: error.message,
  });
  expect(events.at(-1)?.workspaceLabel).toBe("Parent workspace unchanged");
  expect(events.some((event) => event.workspaceVersionId === "must-not-appear")).toBe(false);
});

test("Crash Test includes child recovery evidence with run links and source/run filters", async ({
  page,
}) => {
  const snapshot: CrashSnapshot = {
    project: "durable-agent",
    services_at: at("06"),
    services_error: null,
    controls: [
      {
        id: "sandbox-lifecycle",
        kind: "lifecycle",
        label: "Sandbox started",
        at: at("02"),
        operation_id: "child-edit",
        hook: { event_id: "sandbox-hook", service: "tools" },
      },
    ],
    autoscaling_enabled: true,
    autoscaling_checked_at: null,
    scenarios: [],
    services: [],
    current: {
      id: "scenario",
      scenario: "subagent-recovery",
      status: "running",
      manual_intervention: false,
      run: parent,
      child_runs: [child],
      observed_at: at("06"),
      error: null,
      checks: {},
      checkpoints: {
        "child-edit": { completed_at: at("03"), workspace_version_id: "child-saved" },
      },
      events: [
        {
          id: "sandbox-session",
          kind: "hook",
          label: "sandbox.started",
          at: at("02"),
          operation_id: "child-edit",
          hook: { event_id: "sandbox-hook", service: "tools" },
        },
        {
          id: "kill",
          kind: "kill_observed",
          label: "Worker stopped",
          at: at("04"),
          operation_id: "child-edit",
        },
      ],
    },
  };
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await expect(timeline).toContainText("App · Subagent created");
  await expect(timeline).toContainText("App · Attempt 1 outcome unknown");
  await expect(timeline).toContainText("Saved workspace: child-saved");
  await expect(timeline.getByRole("link", { name: "Review tests" }).first()).toHaveAttribute(
    "href",
    `${process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000"}/runs/child`,
  );
  await expect(timeline.getByRole("link", { name: "Review tests" }).first()).toHaveAttribute(
    "target",
    "_blank",
  );
  await page.getByRole("combobox", { name: "Timeline run" }).selectOption(child.id);
  await expect(timeline.getByRole("link", { name: "Parent agent" })).toHaveCount(0);
  await expect(timeline).toContainText("Worker stopped");
  await expect(timeline.getByText("Tools · Sandbox started", { exact: true })).toHaveCount(1);
  await timeline.getByRole("tab", { name: "Saved state", exact: true }).click();
  await expect(timeline).not.toContainText("Worker stopped");
  await expect(timeline).toContainText("outcome unknown");
  await timeline.getByRole("tab", { name: "Crash Test", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(2);
  await expect(timeline).toContainText("Worker stopped");
  await page.getByRole("combobox", { name: "Timeline run" }).selectOption(parent.id);
  await expect(timeline).toContainText("No events from this source.");
  await timeline.getByRole("tab", { name: "All events", exact: true }).click();
  await expect(timeline).not.toContainText("Attempt 1");
  await page.getByRole("combobox", { name: "Timeline run" }).selectOption("all");
  await page.screenshot({
    path: test.info().outputPath("subagent-crash-timeline.png"),
    fullPage: true,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
