import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { CrashSnapshot } from "../src/lib/crash-test";

export function crashSnapshot(): CrashSnapshot {
  return {
    project: "durable-agent",
    services_at: "2026-10-01T12:00:00Z",
    services_error: null,
    controls: [],
    autoscaling_enabled: true,
    autoscaling_checked_at: null,
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

test("the dashboard homepage omits app navigation", async ({ page }) => {
  let chatRequests = 0;
  await page.route("**/api/conversations", (route) => {
    chatRequests++;
    return route.fulfill({ json: [] });
  });
  await page.route("**/crash-api/**", (route) => route.fulfill({ json: crashSnapshot() }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Crashboard", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Scenario", exact: true })).toBeEnabled();
  await expect(page.getByRole("navigation", { name: "Previous chats" })).toHaveCount(0);
  expect(chatRequests).toBe(0);
  await expect(page.getByRole("link", { name: "Open app" })).toHaveAttribute(
    "href",
    process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000",
  );
  await expect(page).toHaveTitle("Crashboard · Applied Agent");
});

test("autoscaling can change during a scenario and failed updates retain the setting", async ({
  page,
}) => {
  const snapshot = crashSnapshot();
  snapshot.current = {
    id: "active",
    scenario: "baseline",
    status: "running",
    manual_intervention: false,
    run: null,
    observed_at: null,
    error: null,
    checkpoints: {},
    checks: {},
    events: [],
  };
  let fail = false;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/crash-api/**", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().url()).toContain("/autoscaling");
      if (fail) return route.fulfill({ status: 503, json: { detail: "Setting update failed" } });
      snapshot.autoscaling_enabled = route.request().postDataJSON().enabled;
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const toggle = page.getByRole("switch", { name: "Allow autoscaling" });
  await expect(toggle).toBeChecked();
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeEnabled();
  await expect(
    page.getByText("Waiting for Control. Enable Crash Test hooks in the app."),
  ).toBeVisible();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  snapshot.autoscaling_checked_at = "2026-10-02T12:00:00Z";
  await expect(page.getByText(/Last check:/)).toBeVisible();
  fail = true;
  await toggle.click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Setting update failed");
  await expect(toggle).not.toBeChecked();
  fail = false;
  await toggle.click();
  await expect(toggle).toBeChecked();
  expect(errors).toEqual([]);
});

test("scenario selection supports the keyboard and validates an existing run", async ({ page }) => {
  const snapshot = crashSnapshot();
  snapshot.scenarios.push({ name: "baseline", hook: "none", description: "Observe a normal run." });
  const requests: unknown[] = [];
  await page.route("**/crash-api/**", (route) => {
    if (route.request().method() === "POST") requests.push(route.request().postDataJSON());
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const select = page.getByRole("combobox", { name: "Scenario", exact: true });
  await expect(select).toBeEnabled();
  await expect(select).toHaveText("Baseline");
  await select.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Home");
  await expect(
    page.getByRole("option", { name: "Saved response lost", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(select).toHaveText("Saved response lost");
  await expect(select).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("option", { name: "Baseline", exact: true })).toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("option", { name: "Baseline", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(select).toHaveText("Baseline");
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
  await page.goto("/");
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

test("failed action confirmation keeps stale controls locked until a fresh snapshot arrives", async ({
  page,
}) => {
  await page.clock.install();
  const snapshot = crashSnapshot();
  const beforeAction = structuredClone(snapshot);
  let actionRequests = 0;
  let holdPoll = false;
  let oldPollStarted = false;
  let oldPollReturned = false;
  let confirmationFailed = false;
  let recoveryStarted = false;
  let releaseOldPoll!: () => void;
  let releaseRecovery!: () => void;
  const oldPoll = new Promise<void>((resolve) => {
    releaseOldPoll = resolve;
  });
  const recovery = new Promise<void>((resolve) => {
    releaseRecovery = resolve;
  });
  await page.route("**/crash-api/**", async (route) => {
    if (route.request().method() === "POST") {
      actionRequests++;
      snapshot.services[0].running = false;
      return route.fulfill({ json: snapshot.services[0] });
    }
    if (actionRequests) {
      if (!confirmationFailed) {
        confirmationFailed = true;
        return route.fulfill({ status: 503, json: { detail: "Snapshots unavailable" } });
      }
      recoveryStarted = true;
      await recovery;
    } else if (holdPoll) {
      oldPollStarted = true;
      await oldPoll;
      await route.fulfill({ json: beforeAction });
      oldPollReturned = true;
      return;
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const kill = page.getByRole("button", { name: "Kill Tools", exact: true });
  await expect(kill).toBeEnabled();
  holdPoll = true;
  await page.clock.fastForward(1500);
  await expect.poll(() => oldPollStarted).toBe(true);
  await kill.click();
  await expect.poll(() => recoveryStarted).toBe(true);
  const alert = page.getByRole("main").getByRole("alert");
  await expect(kill).toBeDisabled();
  await expect(alert).toHaveText("Snapshots unavailable Showing the last observations.");
  await expect(page.getByText("Last known state", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Arm scenario" })).toBeDisabled();
  await expect(page.getByRole("switch", { name: "Allow autoscaling" })).toBeDisabled();

  releaseOldPoll();
  await expect.poll(() => oldPollReturned).toBe(true);
  await expect(alert).toBeVisible();
  await expect(kill).toBeDisabled();
  releaseRecovery();
  await expect(page.getByRole("button", { name: "Start Tools", exact: true })).toBeEnabled();
  await expect(alert).toHaveCount(0);
  await expect(page.getByText("0 of 1 running", { exact: true })).toBeVisible();
  expect(actionRequests).toBe(1);
});

test("status dots pulse only while their data is live and motion is allowed", async ({ page }) => {
  await page.clock.install();
  const snapshot = crashSnapshot();
  let offline = false;
  let releaseAction!: () => void;
  await page.route("**/crash-api/**", async (route) => {
    if (offline) return route.fulfill({ status: 503, json: { detail: "Controller offline" } });
    if (route.request().method() === "POST") {
      await new Promise<void>((resolve) => {
        releaseAction = resolve;
      });
      snapshot.services[0].running = false;
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const connection = page.locator("header [data-slot='status-dot']");
  const container = page
    .getByRole("article", { name: "durable-agent-tools-1" })
    .locator("[data-slot='status-dot']");
  await expect(connection).toHaveCSS("animation-name", "pulse");
  await expect(container).toHaveCSS("animation-name", "pulse");

  await page.getByRole("button", { name: "Kill Tools", exact: true }).click();
  await expect.poll(() => typeof releaseAction).toBe("function");
  await expect(connection).toHaveCSS("animation-name", "none");
  await expect(container).toHaveCSS("animation-name", "none");
  releaseAction();
  await expect(page.getByText("stopped", { exact: true })).toBeVisible();
  await expect(container).toHaveCSS("animation-name", "pulse");

  snapshot.services_error = "Docker unavailable";
  await page.clock.fastForward(1500);
  await expect(container).toHaveCSS("animation-name", "none");
  await expect(connection).toHaveCSS("animation-name", "pulse");
  offline = true;
  await page.clock.fastForward(1500);
  await expect(connection).toHaveCSS("animation-name", "none");
  offline = false;
  snapshot.services_error = null;
  await page.clock.fastForward(1500);
  await expect(container).toHaveCSS("animation-name", "pulse");
  await expect(connection).toHaveCSS("animation-name", "pulse");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(connection).toHaveCSS("animation-name", "none");
  await expect(container).toHaveCSS("animation-name", "none");
});

test("manual actions during scenarios appear in the timeline, result, and export", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const snapshot = crashSnapshot();
  const session: NonNullable<CrashSnapshot["current"]> = {
    id: "scenario",
    scenario: "baseline",
    status: "running",
    manual_intervention: false,
    run: null,
    observed_at: null,
    error: null,
    checkpoints: {},
    checks: {},
    events: [],
  };
  snapshot.current = session;
  let recovering = false;
  await page.route("**/crash-api/**", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().url()).toContain("/containers/action");
      const body = route.request().postDataJSON();
      expect(body.id).toBe(snapshot.services[0].id);
      expect(body.started_at).toBe(snapshot.services[0].started_at);
      if (recovering)
        return route.fulfill({
          status: 409,
          json: { detail: "A container action is in progress. Try again shortly." },
        });
      snapshot.services[0].running = body.action === "start";
      session.manual_intervention = true;
      session.events.push({
        id: `manual-${body.action}`,
        kind: "manual_action",
        at: "2026-10-01T12:00:00Z",
        label: `Manual ${body.action} requested: tools`,
      });
      return route.fulfill({ json: snapshot.services[0] });
    }
    return route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  const result = page.getByRole("region", { name: "Baseline", exact: true });
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await expect(result.getByText("Manual intervention", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Kill Tools", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start Tools", exact: true })).toBeEnabled();
  await expect(result.getByText("Manual intervention", { exact: true })).toBeVisible();
  await expect(timeline).toContainText("Manual kill requested: tools");
  recovering = true;
  await page.getByRole("button", { name: "Start Tools", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "A container action is in progress",
  );
  await expect(result.getByRole("status")).toHaveText("Waiting for a run");
  recovering = false;
  await page.getByRole("button", { name: "Start Tools", exact: true }).click();
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeEnabled();
  await expect(timeline).toContainText("Manual start requested: tools");
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  session.status = "passed";
  await expect(result.getByRole("status")).toHaveText("passed");
  await expect(result.getByText("Manual intervention", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Export JSON" }).click();
  const evidence = await download;
  const contents = JSON.parse(await readFile((await evidence.path())!, "utf8"));
  expect(contents.current.manual_intervention).toBe(true);
  expect(contents.current.events.map((event: { label: string }) => event.label)).toEqual([
    "Manual kill requested: tools",
    "Manual start requested: tools",
  ]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("scenarios allow manual controls, cancel, and retain observations offline on mobile", async ({
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
          manual_intervention: false,
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
  await page.goto("/");
  await page.getByRole("button", { name: "Arm scenario" }).click();
  await expect(page.getByRole("status")).toHaveText("Waiting for a run");
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeEnabled();
  await expect(page.getByRole("combobox", { name: "Scenario", exact: true })).toBeDisabled();
  snapshot.services_error = "Docker unavailable";
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Docker unavailable");
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeDisabled();
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
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Start Sandbox", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Start Sandbox", exact: true })).toHaveAttribute(
    "title",
    "Tools manages sandbox recovery",
  );
  snapshot.services = [];
  await expect(page.getByText("No containers found.", { exact: true })).toBeVisible();
});

test("lifecycle observations appear without a scenario and are not duplicated by one", async ({
  page,
}) => {
  const snapshot = crashSnapshot();
  snapshot.controls = [
    {
      id: "kill",
      kind: "kill_observed",
      label: "Process stopped: worker",
      at: "2026-10-01T12:00:00Z",
    },
    {
      id: "worker",
      kind: "lifecycle",
      label: "Worker restarted",
      at: "2026-10-01T12:00:01Z",
      detail: "Worker 1 · container worker-123",
      hook: { event_id: "worker-hook", service: "control" },
    },
    {
      id: "sandbox",
      kind: "lifecycle",
      label: "Sandbox started",
      at: "2026-10-01T12:00:02Z",
      operation_id: "op",
      detail: "Container sandbox-456 · operation op · lease generation 2",
      hook: { event_id: "sandbox-hook", service: "tools" },
    },
  ];
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await expect(timeline.getByRole("listitem")).toHaveCount(3);
  await expect(timeline.getByRole("listitem").nth(1)).toContainText("Control · Worker restarted");
  await expect(timeline.getByRole("listitem").nth(1)).toContainText("worker-123");
  await expect(timeline.getByRole("listitem").nth(2)).toContainText("Tools · Sandbox started");
  await expect(timeline).toContainText("lease generation 2");
  snapshot.current = {
    id: "active",
    scenario: "baseline",
    status: "running",
    manual_intervention: false,
    run: null,
    observed_at: null,
    error: null,
    checkpoints: {},
    checks: {},
    events: [
      { ...snapshot.controls[2], id: "scenario-hook", kind: "hook", label: "sandbox.started" },
    ],
  };
  await expect(page.getByRole("button", { name: "Cancel scenario" })).toBeEnabled();
  await expect(timeline.getByRole("combobox", { name: "Timeline view" })).toHaveText(
    "This scenario",
  );
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  await expect(timeline).toContainText("Tools · Sandbox started");
  await timeline.getByRole("combobox", { name: "Timeline view" }).click();
  await page.getByRole("option", { name: "All activity", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(3);
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Export JSON" }).click();
  const contents = JSON.parse(await readFile((await (await download).path())!, "utf8"));
  expect(contents.controls[2].hook.event_id).toBe("sandbox-hook");
});

test("new scenarios get a focused timeline and keep unrelated activity out of exports", async ({
  page,
}) => {
  await page.clock.install();
  await page.setViewportSize({ width: 320, height: 800 });
  const snapshot = crashSnapshot();
  const { runDetail } = await import("./fixtures");
  snapshot.controls = [
    { id: "old", at: "2026-10-01T11:00:00Z", kind: "manual_action", label: "Earlier manual kill" },
  ];
  snapshot.current = {
    id: "first",
    scenario: "saved-response-lost",
    status: "running",
    manual_intervention: false,
    run: null,
    observed_at: null,
    error: null,
    checkpoints: {},
    checks: {},
    events: [
      {
        id: "armed",
        at: "2026-10-01T12:00:00Z",
        kind: "armed",
        label: "Scenario armed: saved-response-lost",
      },
    ],
  };
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  const view = timeline.getByRole("combobox", { name: "Timeline view" });
  await expect(view).toHaveText("This scenario");
  await expect(timeline).toContainText("Saved response lost · Waiting for a run");
  await expect(timeline).not.toContainText("Earlier manual kill");
  await expect(timeline.locator("[data-slot='status-dot']").first()).toHaveCSS(
    "animation-name",
    "pulse",
  );
  snapshot.current.run = {
    ...runDetail,
    status: "running",
    operations: [
      {
        id: "current-op",
        position: 0,
        kind: "tool",
        tool_name: "write_file",
        created_at: "2026-10-01T12:00:01Z",
        completed_at: null,
        result: null,
        attempts: [],
      },
    ],
  };
  snapshot.controls.push(
    {
      id: "related",
      kind: "lifecycle",
      at: "2026-10-01T12:00:02Z",
      label: "Current sandbox",
      operation_id: "current-op",
      hook: { service: "tools" },
    },
    {
      id: "unrelated",
      kind: "lifecycle",
      at: "2026-10-01T12:00:02Z",
      label: "Unrelated sandbox",
      operation_id: "other-op",
      hook: { service: "tools" },
    },
  );
  await page.clock.fastForward(1500);
  await expect(timeline).toContainText("Saved response lost · Running");
  await expect(timeline).toContainText("Current sandbox");
  await expect(timeline).not.toContainText("Unrelated sandbox");
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Export JSON" }).click();
  const contents = JSON.parse(await readFile((await (await download).path())!, "utf8"));
  expect(contents.controls.map((event: { id: string }) => event.id)).toEqual(["related"]);
  await view.click();
  await page.getByRole("option", { name: "All activity", exact: true }).click();
  await expect(timeline).toContainText("Earlier manual kill");
  await expect(timeline).toContainText("Unrelated sandbox");
  await timeline.getByRole("tab", { name: "Saved state", exact: true }).click();
  snapshot.current = {
    ...snapshot.current,
    id: "second",
    run: null,
    scenario: "baseline",
    events: [
      {
        id: "second-arm",
        kind: "armed",
        at: "2026-10-01T12:00:03Z",
        label: "Scenario armed: baseline",
      },
    ],
  };
  await page.clock.fastForward(1500);
  await expect(view).toHaveText("This scenario");
  await expect(timeline.getByRole("tab", { name: "All events", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  await expect(timeline).toContainText("Scenario armed: baseline");
  snapshot.current.status = "passed";
  await page.clock.fastForward(1500);
  await expect(timeline).toContainText("Baseline · Finished · passed");
  await expect(timeline.locator("[data-slot='status-dot']").first()).toHaveCSS(
    "animation-name",
    "none",
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
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
    manual_intervention: false,
    observed_at: "2026-10-01T12:00:03Z",
    error: "Control unavailable",
    checks: { "Run completed successfully": true, "Requested fault was observed": false },
    run: {
      ...runDetail,
      status: "succeeded",
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
  await page.goto("/");
  await expect(page).toHaveTitle("Crashboard · Applied Agent");
  const result = page.getByRole("region", { name: "Saved response lost", exact: true });
  await expect(result.getByRole("status")).toHaveText("inconclusive");
  await expect(result.getByRole("list", { name: "Recovery checks" })).toContainText(
    "Failed: Requested fault was observed",
  );
  await expect(result.getByRole("link", { name: "Open run" })).toHaveAttribute(
    "href",
    `${process.env.CRASH_TEST_APP_URL ?? "http://localhost:3000"}/runs/${runDetail.id}`,
  );
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await expect(timeline.getByRole("listitem")).toHaveCount(3);
  await expect(timeline.getByRole("listitem").nth(1)).toContainText(
    "Result and workspace saved by Tools",
  );
  await expect(timeline.getByRole("listitem").nth(2)).toContainText("Process stopped: tools");
  await expect(timeline).not.toContainText("Result accepted by Control");
  await timeline.getByRole("tab", { name: "Saved state", exact: true }).click();
  await expect(timeline.getByRole("tab", { name: "Saved state", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(timeline.getByRole("listitem")).toHaveCount(2);
  await expect(timeline).toContainText("saved-workspace");
  await timeline.getByRole("tab", { name: "Crash Test", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Export JSON" }).click();
  const evidence = await download;
  expect(evidence.suggestedFilename()).toBe("crash-test-scenario.json");
  const contents = JSON.parse(await readFile((await evidence.path())!, "utf8"));
  expect(contents.current.checkpoints.op.workspace_version_id).toBe("saved-workspace");
  expect(contents.current.events[0].kind).toBe("kill_observed");
});

test("clearing the timeline survives polling and reloads while preserving evidence", async ({
  page,
}, testInfo) => {
  await page.clock.install();
  await page.setViewportSize({ width: 320, height: 800 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const snapshot = crashSnapshot();
  snapshot.current = {
    id: "first",
    scenario: "saved-response-lost",
    status: "running",
    manual_intervention: false,
    run: null,
    observed_at: null,
    error: null,
    checkpoints: {},
    checks: {},
    events: [
      {
        id: "scenario-hook",
        kind: "hook",
        label: "sandbox.started",
        at: "2026-10-01T12:00:00Z",
        hook: { event_id: "sandbox-hook", service: "tools" },
      },
    ],
  };
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  const clear = timeline.getByRole("button", { name: "Clear view", exact: true });
  const restore = timeline.getByRole("button", { name: "Show earlier", exact: true });
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  await clear.click();
  await expect(timeline).toContainText("Timeline cleared. New events will appear here.");
  await expect(clear).toBeDisabled();
  // The same hook can later arrive in the controller-wide lifecycle journal.
  snapshot.controls = [
    {
      ...snapshot.current.events[0],
      id: "controller-hook",
      kind: "lifecycle",
      label: "Sandbox started",
    },
  ];
  await page.clock.fastForward(1500);
  await expect(timeline.getByRole("listitem")).toHaveCount(0);
  // A delayed observation remains new even when its timestamp precedes the cleared entry.
  snapshot.current.events.push({
    id: "delayed",
    kind: "armed",
    label: "Scenario armed",
    at: "2026-10-01T11:59:59Z",
  });
  await page.clock.fastForward(1500);
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  await expect(timeline.getByRole("listitem")).toContainText("Scenario armed");
  await page.reload();
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
  await expect(timeline).toContainText("1 earlier entry hidden.");
  const download = page.waitForEvent("download");
  await timeline.getByRole("button", { name: "Export JSON" }).click();
  const contents = JSON.parse(await readFile((await (await download).path())!, "utf8"));
  expect(contents.controls[0].hook.event_id).toBe("sandbox-hook");
  expect(contents.current.events).toHaveLength(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await timeline.screenshot({ path: testInfo.outputPath("cleared-timeline.png") });
  await restore.click();
  await expect(timeline.getByRole("listitem")).toHaveCount(2);
  await expect(restore).toHaveCount(0);
  await clear.click();
  await expect(timeline.getByRole("listitem")).toHaveCount(0);
  snapshot.current.id = "second";
  await page.clock.fastForward(1500);
  await expect(timeline.getByRole("listitem")).toHaveCount(2);
  await expect(restore).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("clearing still works when tab storage is unavailable and reports the limitation", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => {
      throw new Error("Storage unavailable");
    };
  });
  const snapshot = crashSnapshot();
  snapshot.controls = [
    { id: "manual", kind: "manual_action", label: "Manual kill", at: "2026-10-01T12:00:00Z" },
  ];
  await page.route("**/crash-api/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/");
  const timeline = page.getByRole("region", { name: "Execution timeline" });
  await timeline.getByRole("button", { name: "Clear view", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(0);
  await expect(timeline.getByRole("alert")).toHaveText(
    "This timeline view could not be saved for reloads.",
  );
  await timeline.getByRole("button", { name: "Show earlier", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(1);
});
