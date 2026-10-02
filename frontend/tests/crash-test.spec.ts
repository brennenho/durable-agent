import { expect, test } from "@playwright/test";
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
  await page.getByRole("button", { name: "Kill Tools", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start Tools", exact: true })).toBeEnabled();
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
  await expect(page.getByRole("button", { name: "Kill Tools", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
