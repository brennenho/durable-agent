import { expect, test } from "@playwright/test";
import { conversationDetail } from "./fixtures";
import { childRun, parentRun } from "./subagent-fixtures";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) =>
    route.fulfill({ json: [conversationDetail] }),
  );
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({
      json: {
        workspace_version_id: childRun.workspace_version_id,
        entries: [{ path: "report.md", kind: "file", size_bytes: 20 }],
      },
    }),
  );
});

for (const width of [390, 1440]) {
  test(`opens a child and returns to the parent at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const failed = {
      ...parentRun.children[2],
      status: "failed",
      terminal_error: { code: "exhausted", message: "Retry budget exhausted.", retryable: false },
    };
    await page.route(`**/api/conversations/${conversationDetail.id}`, (route) =>
      route.fulfill({
        json: {
          ...conversationDetail,
          status: "waiting",
          runs: [{ ...parentRun, children: [...parentRun.children.slice(0, 2), failed] }],
        },
      }),
    );
    await page.route(`**/api/runs/${childRun.id}`, (route) => route.fulfill({ json: childRun }));
    await page.goto(`/chats/${conversationDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const children = page.getByRole("list", { name: "Subagents", exact: true });
    await expect(children.getByRole("link")).toHaveText(
      parentRun.children.map((child) => child.name),
    );
    await expect(children).toContainText("Retry budget exhausted.");
    await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
    await children.getByRole("link", { name: childRun.parent!.name }).click();
    await expect(page).toHaveURL(`/runs/${childRun.id}`);
    await expect(page.getByLabel("Subagent context")).toContainText(childRun.parent!.name);
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Preview report.md" })).toBeEnabled();
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    await expect(page.getByRole("button", { name: "Branch", exact: true })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath("subagent-detail.png"), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole("link", { name: "Back to parent chat" }).click();
    await expect(page).toHaveURL(`/chats/${conversationDetail.id}`);
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  });
}

test("a child's conversation stays read-only after completion and reload", async ({ page }) => {
  const childConversation = {
    ...conversationDetail,
    id: childRun.id,
    status: "succeeded",
    runs: [{ ...childRun, status: "succeeded", final_answer: "Implementation reviewed." }],
  };
  await page.route(`**/api/conversations/${childRun.id}`, (route) =>
    route.fulfill({ json: childConversation }),
  );
  await page.goto(`/chats/${childRun.id}`);
  await expect(page.getByRole("region", { name: "Final output" })).toHaveText(
    "Implementation reviewed.",
  );
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send message" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("link", { name: "Back to parent chat" })).toHaveAttribute(
    "href",
    `/chats/${conversationDetail.id}`,
  );
  await expect(page.getByRole("button", { name: "Send message" })).toHaveCount(0);
});
