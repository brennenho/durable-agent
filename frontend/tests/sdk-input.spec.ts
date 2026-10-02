import { expect, test } from "@playwright/test";
import type { RunDetail } from "../src/lib/api";
import { conversation, runDetail } from "./fixtures";

const agent = { name: "document_writer", version: "1", app_ref: "test:app", build_id: "test" };
const sdkRun: RunDetail = { ...runDetail, agent, status: "succeeded", prompt: "Old prompt" };
const endpoint = `**/api/runs/${sdkRun.id}`;
test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({
      json: { workspace_version_id: sdkRun.workspace_version_id, entries: [] },
    }),
  );
});

for (const input of [
  null,
  false,
  0,
  "",
  "  ",
  [],
  {},
  { title: "Saved document", content: "Body" },
]) {
  test(`renders exact SDK input ${JSON.stringify(input)}`, async ({ page }) => {
    await page.route(endpoint, (route) => route.fulfill({ json: { ...sdkRun, input } }));
    await page.goto(`/runs/${sdkRun.id}`);
    const message = page.getByLabel("Your message", { exact: true });
    await expect(message.locator("pre")).toHaveText(JSON.stringify(input, null, 2));
    await expect(message).not.toContainText("Old prompt");
  });
}

test("shows SDK strings as plain text, including markup", async ({ page }) => {
  const input = "# Document\n<script>window.injected = true</script>";
  await page.route(endpoint, (route) => route.fulfill({ json: { ...sdkRun, input } }));
  await page.goto(`/runs/${sdkRun.id}`);
  const message = page.getByLabel("Your message", { exact: true });
  await expect(message).toHaveText(input);
  await expect(message.locator("script, h1")).toHaveCount(0);
});

test("preserves legacy prompts and SDK responses without the input field", async ({ page }) => {
  for (const run of [{ ...sdkRun, agent: null, input: null }, sdkRun]) {
    await page.route(endpoint, (route) => route.fulfill({ json: run }));
    await page.goto(`/runs/${sdkRun.id}`);
    await expect(page.getByLabel("Your message", { exact: true })).toHaveText("Old prompt");
  }
});

for (const width of [390, 1440]) {
  test(`renders saved follow-up inputs at ${width}px without blank bubbles`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.route(`**/api/conversations/${conversation.id}`, (route) =>
      route.fulfill({
        json: {
          ...conversation,
          status: "succeeded",
          runs: [
            { ...sdkRun, prompt: "", input: { title: "Original", body: "x".repeat(200) } },
            {
              ...sdkRun,
              id: "follow-up",
              prompt: "",
              input: { title: "Follow-up", body: "Updated text" },
            },
          ],
        },
      }),
    );
    await page.goto(`/chats/${conversation.id}`);
    const messages = page.getByLabel("Your message", { exact: true });
    await expect(messages).toHaveCount(2);
    await expect(messages.nth(0)).toContainText('"title": "Original"');
    await expect(messages.nth(1)).toContainText('"title": "Follow-up"');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/sdk-input-${width}.png`, fullPage: true });
  });
}

for (const name of ["document_writer", "builtin", null]) {
  test(`${name ?? "legacy"} exposes only compatible submission controls`, async ({ page }) => {
    let current: RunDetail = {
      ...sdkRun,
      agent: name ? { ...agent, name } : null,
      input: "Input",
      operations: [
        {
          id: "checkpoint",
          position: 0,
          kind: "tool",
          tool_name: "save_document",
          created_at: sdkRun.created_at,
          completed_at: sdkRun.created_at,
          attempts: [],
          checkpoint: { workspace_version_id: sdkRun.workspace_version_id },
          result: {
            kind: "tool",
            call_id: "save",
            workspace_version_id: sdkRun.workspace_version_id,
            output: { status: "ok", value: { saved: true } },
          },
        },
      ],
    };
    await page.route(`**/api/conversations/${conversation.id}`, (route) =>
      route.fulfill({
        json: {
          ...conversation,
          status: current.status,
          runs: [current],
        },
      }),
    );
    await page.goto(`/chats/${conversation.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const custom = name === "document_writer";
    await expect(page.getByRole("button", { name: "Branch", exact: true })).toHaveCount(
      custom ? 0 : 1,
    );
    await expect(page.getByRole("form", { name: "Reply", exact: true })).toHaveCount(
      custom ? 0 : 1,
    );
    await expect(
      page.getByText("Continue or branch this agent through the SDK.", { exact: true }),
    ).toHaveCount(custom ? 1 : 0);
    if (custom) {
      current = { ...current, status: "running" };
      await page.reload();
      await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Files", exact: true })).toBeVisible();
    }
  });
}

test("renders exact input for inherited and current SDK branch messages", async ({ page }) => {
  const source: RunDetail = {
    ...sdkRun,
    id: "source",
    prompt: "",
    input: { title: "Inherited document" },
    operations: [
      {
        id: "saved-tool",
        position: 0,
        kind: "tool",
        tool_name: "save_document",
        created_at: sdkRun.created_at,
        completed_at: sdkRun.created_at,
        attempts: [],
        result: {
          kind: "tool",
          call_id: "save",
          workspace_version_id: sdkRun.workspace_version_id,
          output: { status: "ok", value: { saved: true } },
        },
      },
    ],
  };
  await page.route(`**/api/conversations/${conversation.id}`, (route) =>
    route.fulfill({
      json: {
        ...conversation,
        status: "succeeded",
        runs: [{ ...sdkRun, prompt: "", input: false }],
        forked_from: {
          conversation_id: "source-chat",
          run_id: source.id,
          operation_id: "saved-tool",
          position: 0,
        },
      },
    }),
  );
  await page.route("**/api/conversations/source-chat", (route) =>
    route.fulfill({
      json: {
        ...conversation,
        id: "source-chat",
        runs: [source],
      },
    }),
  );
  await page.goto(`/chats/${conversation.id}`);
  await expect(page.getByLabel("Earlier message", { exact: true })).toContainText(
    '"title": "Inherited document"',
  );
  await expect(page.getByLabel("Your message", { exact: true })).toHaveText("false");
  await expect(page.getByRole("alert", { name: "Inherited history error" })).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Branched from step 1 in the source chat" }),
  ).toBeVisible();
});
