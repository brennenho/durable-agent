import { expect, test } from "@playwright/test";
import type { ConversationDetail, RunFileListing } from "../src/lib/api";
import { conversationDetail, runDetail } from "./fixtures";

const first = {
  ...runDetail,
  status: "succeeded" as const,
  final_answer: "Saved the first draft.",
};
const second = {
  ...first,
  id: "00000000-0000-4000-8000-000000000003",
  prompt: "Revise the document.",
  final_answer: "Saved the revision.",
  workspace_version_id: "00000000-0000-4000-8000-000000000004",
};
const detail: ConversationDetail = {
  ...conversationDetail,
  status: "succeeded",
  latest_run_id: second.id,
  runs: [first, second],
};
const longPath = "documents/research/a-long-filename-that-should-stay-within-the-file-rail.md";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [detail] }));
  await page.route(`**/api/conversations/${detail.id}`, (route) => route.fulfill({ json: detail }));
  for (const run of [first, second]) {
    const files: RunFileListing = {
      workspace_version_id: run.workspace_version_id,
      entries: [
        { path: "README.md", kind: "file", size_bytes: 15 },
        { path: longPath, kind: "file", size_bytes: 15 },
      ],
    };
    await page.route(`**/api/runs/${run.id}/files`, (route) => route.fulfill({ json: files }));
    await page.route(`**/api/runs/${run.id}/file?**`, (route) =>
      route.fulfill({ contentType: "text/plain", body: run.final_answer }),
    );
  }
});

test("shows a vertical rail beside chat and previews/downloads the selected turn", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/chats/${detail.id}`);
  const rail = page.getByRole("complementary", { name: "Chat files" });
  const preview = rail.getByRole("button", { name: "Preview README.md", exact: true });
  await expect(preview).toBeVisible();
  const chatBox = await page.getByRole("article", { name: "Chat turn" }).first().boundingBox();
  const railBox = await rail.boundingBox();
  expect(railBox!.x).toBeGreaterThanOrEqual(chatBox!.x + chatBox!.width);
  const firstBox = await preview.boundingBox();
  const nextBox = await rail
    .getByRole("button", { name: `Preview ${longPath}`, exact: true })
    .boundingBox();
  expect(nextBox!.y).toBeGreaterThan(firstBox!.y);
  await expect(page.getByRole("article").getByRole("region", { name: "Saved files" })).toHaveCount(
    0,
  );
  await preview.click();
  await expect(page.getByRole("dialog")).toContainText(second.final_answer);
  await page.getByRole("button", { name: "Close preview" }).click();
  await rail.getByRole("combobox", { name: "File version" }).selectOption(first.id);
  const requested = page.waitForRequest((request) =>
    request.url().includes(`/api/runs/${first.id}/file?`),
  );
  await preview.click();
  expect(new URL((await requested).url()).searchParams.get("version_id")).toBe(
    first.workspace_version_id,
  );
  await expect(page.getByRole("dialog")).toContainText(first.final_answer);
  const download = page.waitForEvent("download");
  await page.getByRole("dialog").getByRole("button", { name: "Download", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("README.md");
});

test("stacks files above the reply input on mobile without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/chats/${detail.id}`);
  const rail = page.getByRole("complementary", { name: "Chat files" });
  await expect(
    rail.getByRole("button", { name: `Preview ${longPath}`, exact: true }),
  ).toBeVisible();
  const chatBox = await page.getByRole("article", { name: "Chat turn" }).last().boundingBox();
  const railBox = await rail.boundingBox();
  expect(railBox!.y).toBeGreaterThanOrEqual(chatBox!.y + chatBox!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.getByRole("form", { name: "Reply" }).scrollIntoViewIfNeeded();
  await rail.getByRole("button", { name: "Preview README.md", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(second.final_answer);
  await page.keyboard.press("Escape");
  await expect(rail.getByRole("button", { name: "Preview README.md", exact: true })).toBeFocused();
});

test("keeps an earlier selection when a newer turn appears", async ({ page }) => {
  await page.goto(`/chats/${detail.id}`);
  const version = page.getByRole("combobox", { name: "File version" });
  await version.selectOption(first.id);
  const third = { ...second, id: "00000000-0000-4000-8000-000000000005" };
  await page.route(`**/api/runs/${third.id}/files`, (route) =>
    route.fulfill({
      json: {
        workspace_version_id: third.workspace_version_id,
        entries: [{ path: "latest.txt", kind: "file", size_bytes: 0 }],
      },
    }),
  );
  await page.route(`**/api/conversations/${detail.id}`, (route) =>
    route.fulfill({ json: { ...detail, latest_run_id: third.id, runs: [...detail.runs, third] } }),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("article", { name: "Chat turn" })).toHaveCount(3);
  await expect(version).toHaveValue(first.id);
  await expect(page.getByRole("button", { name: "Preview latest.txt" })).toHaveCount(0);
  await version.selectOption("");
  await expect(page.getByRole("button", { name: "Preview latest.txt" })).toBeVisible();
});
