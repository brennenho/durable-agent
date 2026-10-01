import { expect, test } from "@playwright/test";
import { run, runDetail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/runs", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({
      json: {
        workspace_version_id: runDetail.workspace_version_id,
        entries: [],
      },
    }),
  );
});

const url = `/runs/${runDetail.id}`;
const endpoint = `**/api/runs/${runDetail.id}`;

for (const status of [408, 429, 503]) {
  test(`recovers from HTTP ${status} and stops after the final answer`, async ({ page }) => {
    let requests = 0;
    await page.clock.install();
    let phase = "running";
    await page.route(endpoint, (route) => {
      requests += 1;
      if (phase === "error")
        return route.fulfill({
          status,
          json: {
            error: { code: "unavailable", message: "Database unavailable.", retryable: true },
          },
        });
      return route.fulfill({
        json:
          phase === "running"
            ? { ...runDetail, status: "running", next_attempt_at: null }
            : {
                ...runDetail,
                status: "succeeded",
                next_attempt_at: null,
                finished_at: "2026-10-01T12:01:00Z",
                final_answer: "Done.\n<script>not executable</script>",
              },
      });
    });
    await page.goto(url);
    await expect(page.getByRole("status")).toHaveText("Status: running");
    phase = "error";
    await page.clock.fastForward(2_000);
    await expect(page.getByRole("alert", { name: "Run update error" })).toContainText(
      "Database unavailable.",
    );
    await expect(page.getByRole("status")).toHaveText("Status: running");
    phase = "succeeded";
    await page.clock.fastForward(2_000);
    await expect(page.getByRole("status")).toHaveText("Status: succeeded");
    await expect(page.getByRole("region", { name: "Final output" })).toContainText(
      "<script>not executable</script>",
    );
    await expect(page.getByRole("alert", { name: "Run update error" })).toHaveCount(0);
    const completedCount = requests;
    await page.clock.fastForward(10_000);
    expect(requests).toBe(completedCount);
  });
}

test("displays terminal failure without continuing to poll", async ({ page }) => {
  let requests = 0;
  await page.clock.install();
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill({
      json: {
        ...runDetail,
        status: "failed",
        next_attempt_at: null,
        finished_at: "2026-10-01T12:01:00Z",
        terminal_error: {
          code: "attempts_exhausted",
          message: "All attempts failed.",
          retryable: false,
        },
      },
    });
  });
  await page.goto(url);
  await expect(page.getByRole("alert", { name: "Run failure" })).toHaveText("All attempts failed.");
  const completedCount = requests;
  await page.clock.fastForward(10_000);
  expect(requests).toBe(completedCount);
});

test("shows unknown runs as errors", async ({ page }) => {
  await page.route(endpoint, (route) =>
    route.fulfill({
      status: 404,
      json: { error: { code: "not_found", message: "Run not found.", retryable: false } },
    }),
  );
  await page.goto(url);
  await expect(page.getByRole("alert", { name: "Run update error" })).toHaveText("Run not found.");
  await expect(page.getByRole("heading", { name: "Run details" })).toHaveCount(0);
});

test("stops polling when navigating away", async ({ page }) => {
  let requests = 0;
  await page.clock.install();
  await page.route(endpoint, (route) => {
    requests += 1;
    return route.fulfill({ json: runDetail });
  });
  await page.goto(url);
  await expect(page.getByRole("status")).toHaveText("Status: queued");
  await page.getByRole("link", { name: "New run" }).click();
  await expect(page.getByRole("heading", { name: "Start a run" })).toBeVisible();
  const count = requests;
  await page.clock.fastForward(10_000);
  expect(requests).toBe(count);
});

test("opens the saved run from the submission confirmation", async ({ page }) => {
  await page.route("**/api/runs", (route) =>
    route.fulfill({ json: route.request().method() === "GET" ? [] : run }),
  );
  await page.route(endpoint, (route) => route.fulfill({ json: runDetail }));
  await page.goto("/");
  await page.getByRole("textbox").fill(run.prompt);
  await page.getByRole("button", { name: "Start run" }).click();
  await page.getByRole("link", { name: "View run" }).click();
  await expect(page).toHaveURL(new RegExp(`${run.id}$`));
  await expect(page.getByRole("heading", { name: "Run details" })).toBeVisible();
});

test("switches between result and history with the keyboard", async ({ page }) => {
  await page.route(endpoint, (route) => route.fulfill({ json: runDetail }));
  await page.goto(url);
  const result = page.getByRole("tab", { name: "Result", exact: true });
  await expect(result).toHaveAttribute("aria-selected", "true");
  await result.focus();
  await result.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Execution history" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Final output" })).toBeHidden();
  await page.getByRole("tab", { name: "History" }).press("ArrowLeft");
  await expect(page.getByRole("region", { name: "Final output" })).toBeVisible();
});
