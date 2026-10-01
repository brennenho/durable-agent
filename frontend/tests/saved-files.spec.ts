import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { RunFileListing } from "../src/lib/api";
import { runDetail } from "./fixtures";

const path = "src/a & b.py";
const files: RunFileListing = {
  workspace_version_id: "00000000-0000-4000-8000-000000000020",
  entries: [
    { path: "src", kind: "directory" },
    { path, kind: "file", size_bytes: 12 },
  ],
};
const endpoint = `**/api/runs/${runDetail.id}`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/runs", (route) => route.fulfill({ json: [] }));
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        status: "succeeded",
        next_attempt_at: null,
        finished_at: "2026-10-01T12:01:00Z",
      },
    }),
  );
});

test("downloads the listed version and encodes the file path", async ({ page }) => {
  let requested: URL | undefined;
  await page.route(`${endpoint}/files`, (route) => route.fulfill({ json: files }));
  await page.route(`${endpoint}/file?**`, (route) => {
    requested = new URL(route.request().url());
    return route.fulfill({ contentType: "text/plain", body: "print('ok')\n" });
  });
  await page.goto(`/runs/${runDetail.id}`);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: `Download ${path}` }).click();
  const download = await downloadEvent;
  expect(requested?.searchParams.get("version_id")).toBe(files.workspace_version_id);
  expect(requested?.searchParams.get("path")).toBe(path);
  expect(download.suggestedFilename()).toBe("a & b.py");
  expect(await readFile((await download.path())!, "utf8")).toBe("print('ok')\n");
});

test("shows a download failure instead of saving an error response", async ({ page }) => {
  let downloads = 0;
  page.on("download", () => {
    downloads += 1;
  });
  await page.route(`${endpoint}/files`, (route) => route.fulfill({ json: files }));
  await page.route(`${endpoint}/file?**`, (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: { code: "unavailable", message: "File storage unavailable.", retryable: true },
      },
    }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: `Download ${path}` }).click();
  await expect(page.getByRole("alert", { name: "File download error" })).toHaveText(
    "File storage unavailable.",
  );
  await expect(page.getByRole("button", { name: `Download ${path}` })).toBeEnabled();
  expect(downloads).toBe(0);
});

test("retries a failed listing and shows an empty workspace", async ({ page }) => {
  let unavailable = true;
  await page.route(`${endpoint}/files`, (route) =>
    unavailable
      ? route.fulfill({ status: 503, body: "Unavailable" })
      : route.fulfill({ json: { ...files, entries: [] } }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await expect(page.getByRole("alert", { name: "File listing error" })).toContainText("503");
  unavailable = false;
  await page.getByRole("button", { name: "Retry file listing" }).click();
  await expect(page.getByRole("region", { name: "Saved files" })).toContainText(
    "No files saved yet.",
  );
});

test("refreshes saved files without interrupting a download of the previous version", async ({
  page,
}) => {
  let changed = false;
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requested: URL | undefined;
  const nextVersion = "00000000-0000-4000-8000-000000000021";
  await page.clock.install();
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        status: "running",
        next_attempt_at: null,
        workspace_version_id: changed ? nextVersion : runDetail.workspace_version_id,
      },
    }),
  );
  await page.route(`${endpoint}/files`, (route) =>
    route.fulfill({
      json: changed
        ? {
            workspace_version_id: nextVersion,
            entries: [{ path: "result.md", kind: "file", size_bytes: 5 }],
          }
        : files,
    }),
  );
  await page.route(`${endpoint}/file?**`, async (route) => {
    requested = new URL(route.request().url());
    await ready;
    await route.fulfill({ contentType: "text/plain", body: "original version" });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await expect(page.getByRole("button", { name: `Download ${path}` })).toBeVisible();
  await page.getByRole("button", { name: `Download ${path}` }).click();
  await expect
    .poll(() => requested?.searchParams.get("version_id"))
    .toBe(files.workspace_version_id);
  changed = true;
  await page.getByRole("tab", { name: "History" }).click();
  await page.clock.fastForward(2_000);
  await page.getByRole("tab", { name: "Result", exact: true }).click();
  await expect(page.getByRole("button", { name: "Download result.md" })).toBeVisible();
  await expect(page.getByRole("button", { name: `Download ${path}` })).toHaveCount(0);
  const downloaded = page.waitForEvent("download", { timeout: 3_000 });
  release();
  const download = await downloaded;
  expect(await readFile((await download.path())!, "utf8")).toBe("original version");
});
