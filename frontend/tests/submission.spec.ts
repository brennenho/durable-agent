import { expect, test } from "@playwright/test";
import type { SubmitRunRequest } from "../src/lib/api";
import { run } from "./fixtures";

test("submits a prompt once and displays the saved run", async ({ page }) => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  const bodies: SubmitRunRequest[] = [];
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    bodies.push(route.request().postDataJSON());
    await ready;
    await route.fulfill({ json: run });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Start run" }).click();
  expect(bodies).toHaveLength(0);
  await page.getByLabel("What should the agent do?").fill(run.prompt);
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("button", { name: "Submitting…" })).toBeDisabled();
  await expect(page.getByRole("textbox")).toBeDisabled();
  release();
  await expect(page.getByRole("heading", { name: "Run submitted" })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Status: queued");
  expect(bodies).toHaveLength(1);
  expect(bodies[0].prompt).toBe(run.prompt);
  expect(bodies[0].submission_key).toMatch(/^[0-9a-f-]{36}$/);
});

test("retries a lost response with the same key; a new run gets a new key", async ({ page }) => {
  const bodies: SubmitRunRequest[] = [];
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) await route.abort();
    else await route.fulfill({ json: run });
  });
  await page.goto("/");
  await page.getByRole("textbox").fill(run.prompt);
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("region", { name: "Start a run" }).getByRole("alert")).toContainText(
    "Could not confirm submission",
  );
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByRole("button", { name: "New run" }).click();
  await page.getByRole("textbox").fill(run.prompt);
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("heading", { name: "Run submitted" })).toBeVisible();
  expect(bodies[1]).toEqual(bodies[0]);
  expect(bodies[2].submission_key).not.toBe(bodies[0].submission_key);
});

test("shows the unfinished API's error without claiming success", async ({ page }) => {
  await page.route("**/api/runs", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: [] })
      : route.fulfill({
          status: 501,
          json: {
            error: {
              code: "not_implemented",
              message: "Run submission is not implemented.",
              retryable: false,
            },
          },
        }),
  );
  await page.goto("/");
  await page.getByRole("textbox").fill(run.prompt);
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("region", { name: "Start a run" }).getByRole("alert")).toHaveText(
    "Run submission is not implemented.",
  );
  await expect(page.getByRole("textbox")).toHaveValue(run.prompt);
  await expect(page.getByRole("heading", { name: "Run submitted" })).toHaveCount(0);
});
