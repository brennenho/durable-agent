import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { conversation, conversationDetail, runDetail } from "./fixtures";

const at = (seconds: string) => `2026-10-01T12:00:${seconds}Z`;
const operation: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000011",
  position: 0,
  kind: "tool",
  tool_name: "run_command",
  tool_target: "python -m pytest",
  created_at: at("01"),
  completed_at: at("14"),
  result: {
    kind: "tool",
    call_id: "test",
    workspace_version_id: runDetail.workspace_version_id,
    output: { status: "ok", value: { exit_code: 1, stdout: "1 failed", stderr: "" } },
  },
  attempts: [
    {
      id: "00000000-0000-4000-8000-000000000012",
      attempt_no: 1,
      status: "unknown",
      started_at: at("02"),
      finished_at: at("10"),
      error: null,
    },
    {
      id: "00000000-0000-4000-8000-000000000013",
      attempt_no: 2,
      status: "accepted",
      started_at: at("10"),
      finished_at: at("14"),
      error: null,
    },
  ],
};
const completed: RunDetail = {
  ...runDetail,
  status: "succeeded",
  finished_at: at("14"),
  next_attempt_at: null,
  operations: [operation],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/runs/*/operations/*/checkpoint", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
});

for (const width of [320, 390, 1440]) {
  test(`groups timestamps and duration left of timeline dots at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: completed }));
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const timeline = page.getByRole("list", { name: "Run timeline", exact: true });
    await expect(timeline.locator("li > div > time")).toHaveCount(3);
    const step = page.getByLabel("1. run_command", { exact: true });
    const name = await step.getByText("run_command", { exact: true }).boundingBox();
    const status = await step.getByText("Completed", { exact: true }).boundingBox();
    expect(status!.x >= name!.x + name!.width || status!.y >= name!.y + name!.height).toBe(true);
    const duration = timeline.getByTitle("Latest attempt duration");
    await expect(duration).toHaveText("4s");
    await expect(step.getByTitle("Latest attempt duration")).toHaveCount(0);
    const durationBounds = await duration.boundingBox();
    const clockBounds = await timeline.getByLabel(/^Operation planned:/).boundingBox();
    expect(durationBounds!.y).toBeGreaterThanOrEqual(clockBounds!.y + clockBounds!.height);
    expect(
      Math.abs(durationBounds!.x + durationBounds!.width - (clockBounds!.x + clockBounds!.width)),
    ).toBeLessThan(1);
    await page.screenshot({ path: test.info().outputPath("timing-gutter.png"), fullPage: true });
    await step.locator(":scope > summary").click();
    await page.getByRole("tab", { name: "Timeline", exact: true }).click();
    await expect(page.getByRole("list", { name: "Execution milestones" })).toBeVisible();
    if (width < 640) {
      const inspection = await step.getByRole("tablist").boundingBox();
      const timelineBounds = await timeline.boundingBox();
      expect(inspection!.width).toBeGreaterThanOrEqual(timelineBounds!.width - 20);
    }
    for (const stamp of await timeline.locator("li > div > time").all()) {
      await expect(stamp).toHaveText(/^\d{2}:\d{2}:\d{2}$/);
      await expect(stamp).toHaveAttribute("title", /2026/);
      const position = await stamp.evaluate((time) => {
        const text = document.createRange();
        text.selectNodeContents(time);
        const clock = text.getBoundingClientRect();
        const dot =
          time.parentElement!.nextElementSibling!.firstElementChild!.getBoundingClientRect();
        return {
          clockRight: clock.right,
          dotLeft: dot.left,
          alignment: Math.abs(clock.y + clock.height / 2 - (dot.y + dot.height / 2)),
        };
      });
      expect(position.clockRight).toBeLessThan(position.dotLeft);
      expect(position.alignment).toBeLessThan(3);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  });
}

for (const view of ["run", "conversation"]) {
  test(`current status dots follow live polling in the ${view} view`, async ({ page }) => {
    await page.clock.install();
    let offline = false;
    let current: RunDetail = {
      ...runDetail,
      status: "running",
      operations: [
        {
          ...operation,
          result: null,
          completed_at: null,
          attempts: [{ ...operation.attempts[0], status: "running", finished_at: null }],
        },
      ],
    };
    const endpoint =
      view === "run" ? `**/api/runs/${runDetail.id}` : `**/api/conversations/${conversation.id}`;
    await page.route(endpoint, (route) =>
      route.fulfill({
        status: offline ? 503 : 200,
        json: offline
          ? { error: { code: "unavailable", message: "Unavailable", retryable: true } }
          : view === "run"
            ? current
            : { ...conversationDetail, status: current.status, runs: [current] },
      }),
    );
    await page.goto(view === "run" ? `/runs/${runDetail.id}` : `/chats/${conversation.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const dots = page
      .getByRole("list", { name: "Run timeline", exact: true })
      .locator("[data-slot='status-dot']");
    await expect(dots.nth(0)).toHaveCSS("animation-name", "none");
    await expect(dots.nth(1)).toHaveCSS("animation-name", "pulse");
    offline = true;
    await page.clock.fastForward(2000);
    await expect(dots.nth(1)).toHaveCSS("animation-name", "none");
    offline = false;
    await page.clock.fastForward(2000);
    await expect(dots.nth(1)).toHaveCSS("animation-name", "pulse");
    current = completed;
    await page.clock.fastForward(2000);
    await expect(dots.nth(1)).toHaveCSS("animation-name", "none");
  });

  test(`shows persisted milestones in the ${view} view without loading inspection`, async ({
    page,
  }) => {
    let inspections = 0;
    await page.route("**/api/runs/*/operations/*", (route) => {
      inspections++;
      return route.fulfill({ status: 503, json: {} });
    });
    await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: completed }));
    await page.route(`**/api/conversations/${conversation.id}`, (route) =>
      route.fulfill({ json: { ...conversationDetail, status: "succeeded", runs: [completed] } }),
    );
    await page.goto(view === "run" ? `/runs/${runDetail.id}` : `/chats/${conversation.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const run = page.getByRole("list", { name: "Run timeline", exact: true });
    await expect(run.locator(":scope > li").first()).toContainText("Run submitted");
    await expect(run.locator(":scope > li").last()).toContainText("Run completed");
    await page.getByLabel("1. run_command", { exact: true }).locator(":scope > summary").click();
    await page.getByRole("tab", { name: "Timeline", exact: true }).click();
    const events = page.getByRole("list", { name: "Execution milestones" });
    await expect(events.locator("li > div > span")).toHaveText([
      "Operation planned",
      "Attempt 1 started",
      "Attempt 1 outcome unknown",
      "Attempt 2 started",
      "Result accepted by Control",
    ]);
    expect(
      await events
        .locator("time")
        .evaluateAll((times) => times.map((time) => time.getAttribute("datetime"))),
    ).toEqual([at("01"), at("02"), at("10"), at("10"), at("14")]);
    await expect(events).toContainText(`Accepted workspace: ${runDetail.workspace_version_id}`);
    await expect(events).not.toContainText("crash");
    await expect(events).not.toContainText("reused");
    expect(inspections).toBe(0);
    await expect(page.getByRole("tab", { name: "Timeline", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.screenshot({
      path: test.info().outputPath("execution-timeline.png"),
      fullPage: true,
      animations: "disabled",
    });
  });
}

test("an open timeline gains recovery milestones without replacing earlier events", async ({
  page,
}) => {
  await page.clock.install();
  let current: RunDetail = {
    ...runDetail,
    status: "running",
    operations: [
      {
        ...operation,
        result: null,
        completed_at: null,
        attempts: [{ ...operation.attempts[0], status: "running", finished_at: null }],
      },
    ],
  };
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: current }));
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. run_command", { exact: true }).locator(":scope > summary").click();
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  const events = page.getByRole("list", { name: "Execution milestones" });
  await expect(events.locator("li")).toHaveCount(2);
  const ids = await events
    .locator("li")
    .evaluateAll((items) => items.map((item) => item.dataset.eventId));
  current = {
    ...current,
    status: "retrying",
    operations: [{ ...current.operations[0], attempts: [operation.attempts[0]] }],
  };
  await page.clock.fastForward(2000);
  await expect(events).toContainText("Attempt 1 outcome unknown");
  await expect(events).not.toContainText("Result accepted");
  current = completed;
  await page.clock.fastForward(2000);
  await expect(events.locator("li")).toHaveCount(5);
  expect(
    await events
      .locator("li")
      .evaluateAll((items) => items.slice(0, 2).map((item) => item.dataset.eventId)),
  ).toEqual(ids);
  await expect(events).toContainText("From attempt 2.");
  await expect(page.getByText("Run completed", { exact: true })).toBeVisible();
});

test("a failed run retains attempt failure evidence without inventing acceptance", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const error = {
    code: "sandbox_unavailable",
    message: "Sandbox stopped before saving.",
    retryable: true,
  };
  const failed: RunDetail = {
    ...completed,
    status: "failed",
    terminal_error: error,
    operations: [
      {
        ...operation,
        result: null,
        completed_at: null,
        attempts: [{ ...operation.attempts[0], status: "error", error }],
      },
    ],
  };
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: failed }));
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. run_command", { exact: true }).locator(":scope > summary").click();
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  const events = page.getByRole("list", { name: "Execution milestones" });
  await expect(events).toContainText("Attempt 1 failed");
  await expect(events).toContainText(error.message);
  await expect(events).not.toContainText("Result accepted");
  await expect(events).not.toContainText("Accepted workspace");
  await expect(page.getByText("Run failed", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});

test("saved work appears before acceptance, survives tab changes, and stops polling", async ({
  page,
}) => {
  await page.clock.install();
  await page.setViewportSize({ width: 320, height: 900 });
  let current: RunDetail = {
    ...completed,
    status: "retrying",
    finished_at: null,
    operations: [
      { ...operation, result: null, completed_at: null, attempts: [operation.attempts[0]] },
    ],
  };
  let checkpoint: "none" | "offline" | "saved" = "none";
  let reads = 0;
  await page.route(`**/api/runs/${runDetail.id}`, (route) => route.fulfill({ json: current }));
  await page.route("**/api/runs/*/operations/*/checkpoint", (route) => {
    reads++;
    return route.fulfill({
      status: checkpoint === "offline" ? 503 : 200,
      json:
        checkpoint === "saved"
          ? { completed_at: at("05"), workspace_version_id: runDetail.workspace_version_id }
          : null,
    });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. run_command", { exact: true }).locator(":scope > summary").click();
  expect(reads).toBe(0);
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(page.getByText("No saved checkpoint observed yet.")).toBeVisible();
  const events = page.getByRole("list", { name: "Execution milestones" });
  const planned = await events.locator("li").first().getAttribute("data-event-id");
  await page.getByRole("tab", { name: "Output", exact: true }).click();
  const beforeHidden = reads;
  await page.clock.fastForward(6000);
  expect(reads).toBe(beforeHidden);
  checkpoint = "offline";
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Checkpoint update error" })).toContainText(
    "Saved checkpoint unavailable",
  );
  await expect(page.getByText("Last checked:", { exact: false })).toBeVisible();
  await expect(events).toContainText("Attempt 1 outcome unknown");
  await expect(events).not.toContainText("saved by Tools");
  checkpoint = "saved";
  await page.clock.fastForward(2000);
  await expect(events.locator("li > div > span")).toHaveText([
    "Operation planned",
    "Attempt 1 started",
    "Result and workspace saved by Tools",
    "Attempt 1 outcome unknown",
  ]);
  await expect(
    page.getByText("Saved by Tools. Control acceptance has not been observed."),
  ).toBeVisible();
  await expect(events).not.toContainText("Accepted workspace");
  await expect(events.locator("li").first()).toHaveAttribute("data-event-id", planned!);
  await expect(events).toContainText(`Saved workspace: ${runDetail.workspace_version_id}`);
  await expect(events).not.toContainText("reused");
  const savedReads = reads;
  await page.screenshot({
    path: test.info().outputPath("saved-before-acceptance.png"),
    fullPage: true,
    animations: "disabled",
  });
  current = completed;
  await page.clock.fastForward(2000);
  await expect(events).toContainText("Result accepted by Control");
  await expect(
    page.getByText("Saved by Tools. Control acceptance has not been observed."),
  ).toHaveCount(0);
  expect(reads).toBe(savedReads);
  await page.getByRole("tab", { name: "Output", exact: true }).click();
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(events).toContainText("Result and workspace saved by Tools");
  expect(reads).toBe(savedReads);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});

test("model timelines do not request tool checkpoints", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/runs/*/operations/*/checkpoint", (route) => {
    reads++;
    return route.fulfill({ json: null });
  });
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...completed,
        operations: [
          { ...operation, kind: "model", tool_name: null, tool_target: null, result: null },
        ],
      },
    }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page
    .getByRole("list", { name: "Run timeline", exact: true })
    .locator("details > summary")
    .click();
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(page.getByRole("list", { name: "Execution milestones" })).toContainText(
    "Result accepted by Control",
  );
  expect(reads).toBe(0);
});
