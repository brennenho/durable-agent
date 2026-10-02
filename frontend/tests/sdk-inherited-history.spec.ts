import { expect, test } from "@playwright/test";
import type { ConversationDetail, RunDetail } from "../src/lib/api";
import { conversation, runDetail } from "./fixtures";

const agent = { name: "document_writer", version: "1", app_ref: "test:app", build_id: "test" };
function savedRun(id: string, text: string): RunDetail {
  return {
    ...runDetail,
    id,
    agent,
    prompt: text,
    status: "succeeded",
    output: { answer: text },
    operations: [
      {
        id: `${id}-tool`,
        position: 0,
        kind: "tool",
        tool_name: "save_document",
        created_at: runDetail.created_at,
        completed_at: runDetail.created_at,
        attempts: [],
        result: {
          kind: "tool",
          call_id: id,
          workspace_version_id: runDetail.workspace_version_id,
          output: { status: "ok", value: { saved: text } },
        },
      },
    ],
  };
}
function origin(id: string) {
  return { conversation_id: id, run_id: id, operation_id: `${id}-tool`, position: 0 };
}
const oldest = savedRun("oldest", "Nested checkpoint");
const earlier = { ...savedRun("earlier", "Earlier terminal turn"), output: false };
const selected = savedRun("source", "Source instruction");
const later = savedRun("later", "Later source turn");
const source: ConversationDetail = {
  ...conversation,
  id: "source",
  forked_from: origin("oldest"),
  runs: [
    earlier,
    {
      ...selected,
      status: "failed",
      terminal_error: { code: "late", message: "Error after checkpoint", retryable: false },
      output: "Output after checkpoint",
      operations: [...selected.operations, { ...later.operations[0], position: 1 }],
    },
    later,
  ],
};
const branch: ConversationDetail = {
  ...conversation,
  status: "succeeded",
  forked_from: origin("source"),
  runs: [{ ...savedRun("branch", "Branch instruction"), prompt: "" }],
};
const sourcePath = "**/api/conversations/source";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/conversations/${branch.id}`, (route) => route.fulfill({ json: branch }));
  await page.route(sourcePath, (route) => route.fulfill({ json: source }));
  await page.route("**/api/conversations/oldest", (route) =>
    route.fulfill({ json: { ...conversation, id: "oldest", runs: [oldest] } }),
  );
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({
      json: { workspace_version_id: runDetail.workspace_version_id, entries: [] },
    }),
  );
});

for (const width of [390, 1440]) {
  test(`shows tool-first SDK branch history through nested checkpoints at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    if (width === 1440)
      await page.route(sourcePath, (route) =>
        route.fulfill({
          json: {
            ...source,
            runs: source.runs.map((run) => ({ ...run, status: "succeeded", terminal_error: null })),
          },
        }),
      );
    const inspections: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/operations/")) inspections.push(request.url());
    });
    await page.goto(`/chats/${branch.id}`);
    const history = page.getByRole("region", { name: "Inherited history", exact: true });
    const turns = history.getByRole("article", { name: "Inherited chat turn", exact: true });
    await expect(turns).toHaveCount(3);
    await expect(turns.nth(0)).toContainText("Nested checkpoint");
    await expect(turns.nth(1)).toContainText("Earlier terminal turn");
    await expect(turns.nth(1).getByRole("region", { name: "Final output" })).toHaveText("false");
    await expect(history.getByRole("region", { name: "Final output" })).toHaveCount(1);
    await expect(turns.nth(2)).toContainText("Source instruction");
    await turns.nth(2).getByText("Saved tool call · save_document").click();
    await expect(turns.nth(2)).toContainText('"saved": "Source instruction"');
    await expect(history.getByRole("heading", { name: "document_writer · Inherited" })).toHaveCount(
      3,
    );
    await expect(history).not.toContainText(
      /Later source turn|Output after checkpoint|Error after checkpoint/,
    );
    await expect(history.getByRole("button", { name: "Branch", exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert", { name: "Inherited history error" })).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: "Branched from step 1 in the source chat" }),
    ).toHaveAttribute("href", "/chats/source");
    expect(inspections).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/sdk-branch-history-${width}.png`, fullPage: true });
  });
}

test("uses source lineage for SDK builtin branches too", async ({ page }) => {
  await page.route(`**/api/conversations/${branch.id}`, (route) =>
    route.fulfill({
      json: {
        ...branch,
        runs: branch.runs.map((run) => ({ ...run, agent: { ...agent, name: "builtin" } })),
      },
    }),
  );
  await page.goto(`/chats/${branch.id}`);
  await expect(page.getByRole("article", { name: "Inherited chat turn", exact: true })).toHaveCount(
    3,
  );
  await expect(
    page
      .getByRole("article", { name: "Chat turn", exact: true })
      .getByRole("heading", { name: "Applied Agent" }),
  ).toBeVisible();
});

test("shows loading and cancels source history when leaving the branch", async ({ page }) => {
  let release!: () => void;
  const response = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(sourcePath, async (route) => {
    await response;
    await route.fulfill({ json: source });
  });
  await page.goto(`/chats/${branch.id}`);
  await expect(page.getByText("Loading earlier messages…", { exact: true })).toBeVisible();
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().endsWith("/api/conversations/source"),
  );
  await page.getByRole("link", { name: "New chat", exact: true }).click();
  await aborted;
  release();
  await expect(page.getByRole("region", { name: "Inherited history", exact: true })).toHaveCount(0);
});

for (const failure of ["unavailable", "missing checkpoint"] as const) {
  test(`keeps branch output visible when source is ${failure} and retries`, async ({ page }) => {
    let failing = true;
    await page.route(sourcePath, (route) =>
      route.fulfill(
        !failing
          ? { json: source }
          : failure === "unavailable"
            ? { status: 503, json: { error: { message: "Source unavailable." } } }
            : { json: { ...source, runs: [later] } },
      ),
    );
    await page.goto(`/chats/${branch.id}`);
    await expect(page.getByRole("alert", { name: "Inherited history error" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Final output" })).toContainText(
      "Branch instruction",
    );
    failing = false;
    await page.getByRole("button", { name: "Retry history" }).click();
    await expect(
      page.getByRole("article", { name: "Inherited chat turn", exact: true }),
    ).toHaveCount(3);
    await expect(page.getByRole("alert", { name: "Inherited history error" })).toHaveCount(0);
  });
}
