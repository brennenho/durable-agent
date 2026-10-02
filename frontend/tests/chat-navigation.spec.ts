import { expect, test } from "@playwright/test";
import { conversation, conversationDetail, run, runDetail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route(`**/api/conversations/${conversation.id}`, (route) =>
    route.fulfill({ json: conversationDetail }),
  );
  await page.route(`**/api/runs/${run.id}/files`, (route) =>
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
