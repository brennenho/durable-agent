import { expect, test } from "@playwright/test";
import type { SubmitRunRequest } from "../src/lib/api";
import { run, runDetail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route(`**/api/runs/${run.id}`, (route) => route.fulfill({ json: runDetail }));
  await page.route(`**/api/runs/${run.id}/files`, (route) =>
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
});

test("sends a prompt once and opens the saved chat", async ({ page }) => {
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
  await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
  expect(bodies).toHaveLength(0);
  await page.getByLabel("Message", { exact: true }).fill(run.prompt);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("button", { name: "Sending…" })).toBeDisabled();
  await expect(page.getByRole("textbox")).toBeDisabled();
  release();
  await expect(page).toHaveURL(`/runs/${run.id}`);
  await expect(page.getByRole("status")).toHaveText("Waiting to start");
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
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("form", { name: "New chat" }).getByRole("alert")).toContainText(
    "Could not confirm submission",
  );
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page).toHaveURL(`/runs/${run.id}`);
  await page.getByRole("link", { name: "New chat" }).click();
  await page.getByRole("textbox").fill(run.prompt);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page).toHaveURL(`/runs/${run.id}`);
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
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("form", { name: "New chat" }).getByRole("alert")).toHaveText(
    "Run submission is not implemented.",
  );
  await expect(page.getByRole("textbox")).toHaveValue(run.prompt);
  await expect(page.getByRole("heading", { name: "Run submitted" })).toHaveCount(0);
  await expect(page).toHaveURL("/");
});

test("Enter sends while Shift+Enter adds a line break", async ({ page }) => {
  let submitted: SubmitRunRequest | undefined;
  await page.route("**/api/runs", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: run });
  });
  await page.goto("/");
  const field = page.getByRole("textbox", { name: "Message", exact: true });
  await field.fill("Write a parser.");
  await field.press("Shift+Enter");
  await field.pressSequentially("Add tests.");
  expect(submitted).toBeUndefined();
  await field.press("Enter");
  await expect(page).toHaveURL(`/runs/${run.id}`);
  expect(submitted?.prompt).toBe("Write a parser.\nAdd tests.");
});
