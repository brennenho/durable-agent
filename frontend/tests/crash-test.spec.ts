import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { CrashSnapshot } from "../src/lib/crash-test";

export function crashSnapshot(): CrashSnapshot {
  return {
    project: "durable-agent",
    services_at: "2026-10-01T12:00:00Z",
    services_error: null,
    controls: [],
    current: null,
    scenarios: [
      {
        name: "saved-response-lost",
        hook: "tools.after_commit",
        description: "Recover the original saved workspace.",
      },
    ],
    services: [
      {
        id: "tools-id",
        name: "durable-agent-tools-1",
        service: "tools",
        instance: "tools",
        running: true,
        started_at: "2026-10-01T11:00:00Z",
        exit_code: 0,
        health: "healthy",
      },
    ],
  };
}

test("scenario selection supports the keyboard and validates an existing run", async ({ page }) => {
  const snapshot = crashSnapshot();
  snapshot.scenarios.push({ name: "baseline", hook: "none", description: "Observe a normal run." });
  const requests: unknown[] = [];
  await page.route("**/crash-api/**", (route) => {
    if (route.request().method() === "POST") requests.push(route.request().postDataJSON());
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/crash-test");
  const select = page.getByRole("combobox", { name: "Scenario", exact: true });
  await expect(select).toBeEnabled();
  await select.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("option", { name: "baseline", exact: true })).toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("option", { name: "baseline", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(select).toHaveText("baseline");
  await expect(select).toBeFocused();
  await expect(page.getByText("Observe a normal run.", { exact: true })).toBeVisible();
  await page.getByText("Use an existing run", { exact: true }).click();
  const input = page.getByRole("textbox", { name: "Run ID", exact: true });
  await input.fill("invalid-run");
  await page.getByRole("button", { name: "Arm scenario" }).click();
  await expect(input).toBeFocused();
  expect(requests).toHaveLength(0);
  const runId = "11111111-1111-4111-8111-111111111111";
  await input.fill(runId);
  await page.getByRole("button", { name: "Arm scenario" }).click();
  await expect.poll(() => requests).toEqual([{ scenario: "baseline", run_id: runId }]);
});

test("manual controls preserve target identity and expose failed actions", async ({ page }) => {
  const snapshot = crashSnapshot();
  let fail = false;
  await page.route("**/crash-api/**", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.id).toBe("tools-id");
      expect(body.started_at).toBe(snapshot.services[0].started_at);
      if (fail)
        return route.fulfill({
          status: 409,
          json: { detail: "Container restarted since it was selected" },
        });
      snapshot.services[0].running = body.action === "start";
      return route.fulfill({ json: snapshot.services[0] });
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/crash-test");
  await expect(page.getByRole("heading", { name: "Crashboard", exact: true })).toBeVisible();
  await expect(page.getByText("1 of 1 running", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Kill Tools", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start Tools", exact: true })).toBeEnabled();
  await expect(page.getByText("0 of 1 running", { exact: true })).toBeVisible();
  fail = true;
  await page.getByRole("button", { name: "Start Tools", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Container restarted");
  await expect(page.getByText("stopped", { exact: true })).toBeVisible();
});

test("scenarios lock manual controls, cancel, and retain observations offline on mobile", async ({
  page,
}) => {
  const snapshot = crashSnapshot();
  let offline = false;
  await page.setViewportSize({ width: 320, height: 800 });
  await page.route("**/crash-api/**", async (route) => {
    if (offline) return route.fulfill({ status: 503, json: { detail: "Controller offline" } });
    if (route.request().method() === "POST") {
      if (route.request().url().endsWith("/cancel")) snapshot.current!.status = "inconclusive";
      else {
        expect(route.request().postDataJSON()).toEqual({
          scenario: "saved-response-lost",
          run_id: null,
        });
        snapshot.current = {
          id: "scenario",
          scenario: "saved-response-lost",
          status: "running",
          run: null,
          observed_at: null,
          error: null,
          checkpoints: {},
          checks: {},
          events: [],
        };
      }
      return route.fulfill({ status: 202, json: snapshot.current });
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/crash-test");
  await page.getByRole("button", { name: "Arm scenario" }).click();
  await expect(page.getByRole("status")).toHaveText("Waiting for a run");
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeDisabled();
  snapshot.services_error = "Docker unavailable";
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Docker unavailable");
  await expect(page.getByRole("button", { name: "Cancel scenario" })).toBeEnabled();
  await page.getByRole("button", { name: "Cancel scenario" }).click();
  await expect(page.getByRole("status")).toHaveText("inconclusive");
  offline = true;
  await expect(page.getByRole("alert").filter({ hasText: "Controller offline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tools", exact: true })).toBeVisible();
  await expect(page.getByText("Last known state", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("stopped sandboxes cannot be restarted and empty inventory stays explicit", async ({
  page,
}) => {
  const snapshot = crashSnapshot();
  snapshot.services = [
    {
      ...snapshot.services[0],
      id: "sandbox",
      name: "sandbox-1",
      service: "sandbox",
      running: false,
    },
  ];
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/crash-test");
  await expect(page.getByRole("button", { name: "Start Sandbox", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Start Sandbox", exact: true })).toHaveAttribute(
    "title",
    "Tools manages sandbox recovery",
  );
  snapshot.services = [];
  await expect(page.getByText("No containers found.", { exact: true })).toBeVisible();
});

test("timeline combines saved state and observed crashes, filters and downloads evidence", async ({
  page,
}) => {
  const snapshot = crashSnapshot();
  const { runDetail } = await import("./fixtures");
  snapshot.current = {
    id: "scenario",
    scenario: "saved-response-lost",
    status: "inconclusive",
    observed_at: "2026-10-01T12:00:03Z",
    error: "Control unavailable",
    checks: {},
    run: {
      ...runDetail,
      operations: [
        {
          id: "op",
          position: 0,
          kind: "tool",
          tool_name: "write_file",
          tool_target: "result.txt",
          created_at: "2026-10-01T12:00:00Z",
          completed_at: null,
          result: null,
          attempts: [],
        },
      ],
    },
    checkpoints: {
      op: { completed_at: "2026-10-01T12:00:01Z", workspace_version_id: "saved-workspace" },
    },
    events: [
      {
        id: "kill",
        kind: "kill_observed",
        label: "Process stopped: tools",
        at: "2026-10-01T12:00:02Z",
        operation_id: "op",
      },
    ],
  };
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/crash-test");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await expect(timeline.getByRole("listitem")).toHaveCount(3);
  await expect(timeline.getByRole("listitem").nth(1)).toContainText(
    "Result and workspace saved by Tools",
  );
  await expect(timeline.getByRole("listitem").nth(2)).toContainText("Process stopped: tools");
  await expect(timeline).not.toContainText("Result accepted by Control");
  await timeline.getByRole("button", { name: "Saved state", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(2);
  await expect(timeline).toContainText("saved-workspace");
  await timeline.getByRole("button", { name: "Crash observations", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Download observations" }).click();
  const evidence = await download;
  expect(evidence.suggestedFilename()).toBe("crash-test-scenario.json");
  const contents = JSON.parse(await readFile((await evidence.path())!, "utf8"));
  expect(contents.current.checkpoints.op.workspace_version_id).toBe("saved-workspace");
  expect(contents.current.events[0].kind).toBe("kill_observed");
});
