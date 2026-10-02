import { expect, test } from "@playwright/test";
import { conversation, conversationDetail, run, runDetail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route(`**/api/conversations/${conversation.id}`, (route) =>
    route.fulfill({ json: conversationDetail }),
  );
  await page.route(`**/api/runs/${run.id}/files?**`, (route) =>
    route.fulfill({
      json: { workspace_version_id: runDetail.workspace_version_id, entries: [] },
    }),
  );
});

test("opens a previous chat and marks the current one", async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [conversation] }));
  await page.goto("/");
  await expect(page).toHaveTitle("Applied Agent");
  const nav = page.getByRole("navigation", { name: "Previous chats" });
  await nav.getByRole("link", { name: conversation.title }).click();
  await expect(page).toHaveURL(`/chats/${conversation.id}`);
  await expect(nav.getByRole("link", { name: conversation.title })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await nav.getByRole("link", { name: "New chat" }).click();
  await expect(page).toHaveURL("/");
  await expect(nav.getByRole("link", { name: conversation.title })).not.toHaveAttribute(
    "aria-current",
  );
});

test("distinguishes a failed chat list from an empty list and can retry", async ({ page }) => {
  let unavailable = true;
  await page.route("**/api/conversations", (route) =>
    route.fulfill(
      unavailable
        ? {
            status: 503,
            json: {
              error: { code: "unavailable", message: "Could not load chats.", retryable: true },
            },
          }
        : { json: [] },
    ),
  );
  await page.goto("/");
  await expect(page.getByRole("alert", { name: "Chat list error" })).toHaveText(
    "Could not load chats.",
  );
  await expect(page.getByText("Your chats will appear here.")).toHaveCount(0);
  unavailable = false;
  await page.getByRole("button", { name: "Retry chats" }).click();
  await expect(page.getByText("Your chats will appear here.")).toBeVisible();
  await expect(page.getByRole("alert", { name: "Chat list error" })).toHaveCount(0);
});

test("mobile navigation closes after selecting a chat and supports Escape", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [conversation] }));
  await page.goto("/");
  await page.getByRole("button", { name: "Open chats" }).click();
  const dialog = page.getByRole("dialog", { name: "Previous chats" });
  await dialog.getByRole("link", { name: conversation.title }).click();
  await expect(page).toHaveURL(`/chats/${conversation.id}`);
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "Open chats" }).click();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open chats" })).toBeFocused();
});

for (const width of [1440, 390]) {
  test(`updates sidebar activity through pause, resume, and completion at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.clock.install();
    let saved = { ...conversation, status: "running" as typeof conversation.status };
    await page.route("**/api/conversations", (route) => route.fulfill({ json: [saved] }));
    await page.goto("/");
    if (width < 768) await page.getByRole("button", { name: "Open chats" }).click();
    const nav = width < 768 ? page.getByRole("dialog") : page.getByRole("navigation");
    const chat = nav.getByRole("link", { name: conversation.title });
    await expect(chat.getByRole("img", { name: "Active", exact: true })).toBeVisible();

    saved = { ...saved, pause_requested: true };
    await page.clock.fastForward(2_000);
    await expect(chat.getByRole("img", { name: "Pausing", exact: true })).toBeVisible();
    await expect(chat.getByRole("img", { name: "Paused", exact: true })).toHaveCount(0);

    for (const status of ["queued", "retrying"] as const) {
      saved = { ...saved, status };
      await page.clock.fastForward(2_000);
      await expect(chat.getByRole("img", { name: "Paused", exact: true })).toBeVisible();
      saved = { ...saved, pause_requested: false };
      await page.clock.fastForward(2_000);
      await expect(chat.getByRole("img", { name: "Active", exact: true })).toBeVisible();
      saved = { ...saved, pause_requested: true };
    }

    for (const status of ["succeeded", "failed"] as const) {
      saved = { ...saved, status, pause_requested: false };
      await page.clock.fastForward(2_000);
      await expect(chat.getByRole("img")).toHaveCount(0);
    }
    saved = { ...saved, status: "queued" };
    await page.clock.fastForward(2_000);
    await expect(chat.getByRole("img", { name: "Active", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  });
}

test("retains chats on a refresh error and retries without overlapping requests", async ({
  page,
}) => {
  await page.clock.install();
  let requests = 0;
  let unavailable = false;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/conversations", async (route) => {
    requests++;
    if (unavailable) {
      await pending;
      return route.fulfill({ status: 503, body: "Unavailable" });
    }
    return route.fulfill({ json: [conversation] });
  });
  await page.goto("/");
  const chat = page.getByRole("link", { name: conversation.title });
  await expect(chat).toBeVisible();
  const initialRequests = requests;
  unavailable = true;
  await page.clock.fastForward(2_000);
  await expect.poll(() => requests).toBe(initialRequests + 1);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.fastForward(10_000);
  expect(requests).toBe(initialRequests + 1);
  release();
  await expect(page.getByRole("alert", { name: "Chat list error" })).toBeVisible();
  await expect(chat).toBeVisible();
  unavailable = false;
  await page.getByRole("button", { name: "Retry chats" }).click();
  await expect(page.getByRole("alert", { name: "Chat list error" })).toHaveCount(0);
  expect(requests).toBe(initialRequests + 2);
});
