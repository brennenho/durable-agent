import { expect, test } from "@playwright/test";
import { runDetail } from "./fixtures";

const path = "docs/notes & setup.md";
const version = "00000000-0000-4000-8000-000000000020";
const endpoint = `**/api/runs/${runDetail.id}`;
const listing = {
  workspace_version_id: version,
  entries: [{ path, kind: "file", size_bytes: 100 }],
};

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
  await page.route(`${endpoint}/files`, (route) => route.fulfill({ json: listing }));
});

test("previews safe Markdown from the listed version, downloads, and restores focus", async ({
  page,
}) => {
  const requested: URL[] = [];
  await page.route(`${endpoint}/file?**`, (route) => {
    requested.push(new URL(route.request().url()));
    return route.fulfill({
      body: "## Setup\n\n- Install Python\n\n<script>alert(1)</script>\n\n![Image](https://example.com/image.png)",
      contentType: "text/markdown",
    });
  });
  await page.goto(`/runs/${runDetail.id}`);
  const trigger = page.getByRole("button", { name: `Preview ${path}` });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: path });
  await expect(dialog.getByRole("heading", { name: "Setup", exact: true })).toBeVisible();
  await expect(dialog.getByRole("listitem")).toHaveText("Install Python");
  await expect(dialog.locator("script, img")).toHaveCount(0);
  const previewRequests = requested.length;
  expect(previewRequests).toBeGreaterThan(0);
  const downloaded = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toBe("notes & setup.md");
  expect(requested).toHaveLength(previewRequests + 1);
  for (const url of requested) {
    expect(url.searchParams.get("path")).toBe(path);
    expect(url.searchParams.get("version_id")).toBe(version);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("shows code as literal text rather than HTML", async ({ page }) => {
  await page.route(`${endpoint}/files`, (route) =>
    route.fulfill({
      json: {
        ...listing,
        entries: [{ path: "app.py", kind: "file", size_bytes: 100 }],
      },
    }),
  );
  await page.route(`${endpoint}/file?**`, (route) =>
    route.fulfill({ body: 'print("<img src=x onerror=alert(1)>")' }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: "Preview app.py" }).click();
  const dialog = page.getByRole("dialog", { name: "app.py" });
  await expect(dialog.locator("pre")).toHaveText('print("<img src=x onerror=alert(1)>")');
  await expect(dialog.locator("img")).toHaveCount(0);
});

test("shows preview and download failures inside the dialog and retries on reopen", async ({
  page,
}) => {
  let unavailable = true;
  await page.route(`${endpoint}/file?**`, (route) =>
    route.fulfill(unavailable ? { status: 503, body: "Unavailable" } : { body: "" }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: `Preview ${path}` }).click();
  const dialog = page.getByRole("dialog", { name: path });
  await expect(dialog.getByRole("alert")).toContainText("503");
  await dialog.getByRole("button", { name: "Download", exact: true }).click();
  await expect(dialog.getByRole("alert", { name: "Preview download error" })).toContainText("503");
  await page.keyboard.press("Escape");
  unavailable = false;
  await page.getByRole("button", { name: `Preview ${path}` }).click();
  await expect(dialog).toContainText("This file is empty.");
});

for (const [name, body] of [
  ["oversized", Buffer.alloc(256 * 1024 + 1, "a")],
  ["binary", Buffer.from([0, 255, 1])],
] as const) {
  test(`offers download when the response is ${name}`, async ({ page }) => {
    await page.route(`${endpoint}/file?**`, (route) => route.fulfill({ body }));
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByRole("button", { name: `Preview ${path}` }).click();
    const dialog = page.getByRole("dialog", { name: path });
    await expect(dialog).toContainText("This file cannot be previewed.");
    await expect(dialog.getByRole("button", { name: "Download", exact: true })).toBeEnabled();
  });
}

test("does not fetch unsupported files for a preview", async ({ page }) => {
  let requested = false;
  await page.route(`${endpoint}/files`, (route) =>
    route.fulfill({
      json: { ...listing, entries: [{ path: "image.png", kind: "file", size_bytes: 100 }] },
    }),
  );
  await page.route(`${endpoint}/file?**`, (route) => {
    requested = true;
    return route.fulfill({ body: "image" });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: "Preview image.png" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "Preview is unavailable for this file type.",
  );
  expect(requested).toBe(false);
});

test("replaces a pending preview when the saved workspace version changes", async ({ page }) => {
  let changed = false;
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  const nextVersion = "00000000-0000-4000-8000-000000000021";
  await page.clock.install();
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        status: "running",
        next_attempt_at: null,
        workspace_version_id: changed ? nextVersion : version,
      },
    }),
  );
  await page.route(`${endpoint}/files`, (route) =>
    route.fulfill({ json: { ...listing, workspace_version_id: changed ? nextVersion : version } }),
  );
  await page.route(`${endpoint}/file?**`, async (route) => {
    const old = new URL(route.request().url()).searchParams.get("version_id") === version;
    if (old) await ready;
    await route.fulfill({ body: old ? "Old content" : "Newest content" });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByRole("button", { name: `Preview ${path}` }).click();
  const dialog = page.getByRole("dialog", { name: path });
  await expect(dialog.getByRole("status")).toHaveText("Loading preview…");
  const cancelled = page.waitForEvent("requestfailed", (request) =>
    request.url().includes(`/file?version_id=${version}`),
  );
  changed = true;
  await page.clock.fastForward(2_000);
  await cancelled;
  await expect(dialog).toContainText("Newest content");
  release();
  await expect(dialog).not.toContainText("Old content");
});
