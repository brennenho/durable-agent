import { expect, test, type Page } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { conversationDetail, runDetail } from "./fixtures";

const endpoint = `**/api/runs/${runDetail.id}`;
const chatUrl = `/chats/${conversationDetail.id}`;
const operation: RunDetail["operations"][number] = {
  id: "00000000-0000-4000-8000-000000000021",
  kind: "model",
  position: 0,
  created_at: runDetail.created_at,
  completed_at: null,
  result: null,
  attempts: [],
};

async function setup(page: Page, initial: Partial<RunDetail> = {}) {
  const state = { run: { ...runDetail, operations: [operation], ...initial } };
  await page.clock.install();
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/conversations/${conversationDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...conversationDetail,
        status: state.run.status,
        pause_requested: state.run.pause_requested,
        runs: [state.run],
      },
    }),
  );
  await page.route(endpoint, (route) => route.fulfill({ json: state.run }));
  await page.route(`${endpoint}/files?**`, (route) =>
    route.fulfill({
      json: {
        workspace_version_id: state.run.workspace_version_id,
        entries: [{ path: "note.txt", kind: "file", size_bytes: 5 }],
      },
    }),
  );
  return state;
}

for (const width of [1440, 390]) {
  test(`pauses and resumes saved work at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page, { status: "retrying" });
    let release!: () => void;
    const ready = new Promise<void>((resolve) => (release = resolve));
    const actions: string[] = [];
    await page.route(`${endpoint}/*`, async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const action = route.request().url().split("/").at(-1)!;
      actions.push(action);
      await ready;
      state.run = { ...state.run, pause_requested: action === "pause" };
      await route.fulfill({ json: state.run });
    });
    await page.goto(chatUrl);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByRole("button", { name: "Saving…" })).toBeDisabled();
    release();
    await expect(page.getByRole("status", { name: "Execution status" })).toHaveText("Paused");
    await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Preview note.txt" })).toBeEnabled();
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    await expect(page.getByLabel("1. Model call", { exact: true })).toContainText("Paused");
    await expect(page.getByRole("region", { name: "Execution history" })).not.toContainText(
      "Waiting to retry",
    );
    await page.reload();
    await expect(page.getByRole("status", { name: "Execution status" })).toHaveText("Paused");
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(page.getByRole("status", { name: "Execution status" })).toContainText("retry");
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeEnabled();
    expect(actions).toEqual(["pause", "resume"]);
    expect(state.run.operations).toEqual([operation]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}

test("can cancel a pending pause while the current call finishes", async ({ page }) => {
  const state = await setup(page, { status: "running" });
  await page.route(`${endpoint}/pause`, (route) => {
    state.run.pause_requested = true;
    return route.fulfill({ json: state.run });
  });
  await page.route(`${endpoint}/resume`, (route) => {
    state.run.pause_requested = false;
    return route.fulfill({ json: state.run });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("status", { name: "Execution status" })).toHaveText(
    "Pausing after this call…",
  );
  await expect(page.getByText("The current call will finish before pausing.")).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(page.getByRole("status", { name: "Execution status" })).not.toContainText("Paus");
});

for (const action of ["pause", "resume"] as const) {
  test(`a lost ${action} acknowledgment requires confirming the same action`, async ({ page }) => {
    const state = await setup(page, { pause_requested: action === "resume" });
    let requests = 0;
    await page.route(`${endpoint}/${action}`, (route) => {
      state.run.pause_requested = action === "pause";
      requests++;
      return requests === 1 ? route.abort() : route.fulfill({ json: state.run });
    });
    await page.goto(chatUrl);
    await page
      .getByRole("button", { name: action === "pause" ? "Pause" : "Resume", exact: true })
      .click();
    await expect(page.getByRole("alert", { name: "Run control error" })).toContainText(
      `Retry ${action} to confirm.`,
    );
    await page.clock.fastForward(2_000);
    const opposite = action === "pause" ? "Resume" : "Pause";
    await expect(page.getByRole("button", { name: opposite, exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: `Retry ${action}` }).click();
    await expect(page.getByRole("button", { name: opposite, exact: true })).toBeEnabled();
    expect(requests).toBe(2);
  });
}

test("a delayed poll cannot replace the confirmed pause", async ({ page }) => {
  const state = await setup(page);
  let block = false;
  let release!: () => void;
  const ready = new Promise<void>((resolve) => (release = resolve));
  let delayed = false;
  await page.route(`**/api/conversations/${conversationDetail.id}`, async (route) => {
    const snapshot = {
      ...conversationDetail,
      pause_requested: state.run.pause_requested,
      runs: [{ ...state.run }],
    };
    if (block) {
      delayed = true;
      await ready;
    }
    await route.fulfill({ json: snapshot });
  });
  await page.route(`${endpoint}/pause`, (route) => {
    block = false;
    state.run.pause_requested = true;
    return route.fulfill({ json: state.run });
  });
  await page.goto(chatUrl);
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  block = true;
  await page.clock.fastForward(2_000);
  await expect.poll(() => delayed).toBe(true);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("status", { name: "Execution status" })).toHaveText("Paused");
  release();
  await page.clock.fastForward(2_000);
  await expect(page.getByRole("status", { name: "Execution status" })).toHaveText("Paused");
});

test("failed confirmation does not invent paused state", async ({ page }) => {
  const state = await setup(page);
  let unavailable = false;
  await page.route(endpoint, (route) =>
    unavailable
      ? route.fulfill({ status: 503, body: "Unavailable" })
      : route.fulfill({ json: state.run }),
  );
  await page.route(`${endpoint}/pause`, (route) => {
    state.run.pause_requested = true;
    unavailable = true;
    return route.fulfill({ json: state.run });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Run control error" })).toContainText("503");
  await expect(page.getByRole("status", { name: "Execution status" })).not.toHaveText("Paused");
  await expect(page.getByRole("button", { name: "Retry pause" })).toBeEnabled();
});

for (const status of ["succeeded", "failed"] as const) {
  test(`terminal ${status} supersedes a pending pause`, async ({ page }) => {
    const state = await setup(page, { status: "running" });
    await page.route(`${endpoint}/pause`, (route) => {
      state.run = { ...state.run, status, pause_requested: false, final_answer: "Task ended." };
      return route.fulfill({ json: state.run });
    });
    await page.goto(chatUrl);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByRole("status", { name: "Execution status" })).toHaveText(
      status === "succeeded" ? "Completed" : "Stopped",
    );
    await expect(page.getByLabel("Run controls")).toHaveCount(0);
    await expect(page.getByText("Task ended.")).toBeVisible();
  });
}
