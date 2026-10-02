import { expect, test } from "@playwright/test";
import type { components } from "../src/lib/api.generated";
import type { ConversationDetail, RunDetail } from "../src/lib/api";
import { conversation, runDetail } from "./fixtures";

type Inspection = components["schemas"]["OperationInspection"];
const first: RunDetail = {
  ...runDetail,
  prompt: "Try GAMMA instead.",
  operations: [
    {
      id: "00000000-0000-4000-8000-000000000042",
      position: 0,
      kind: "model",
      created_at: runDetail.created_at,
      completed_at: null,
      result: null,
      attempts: [],
    },
  ],
};
const branch: ConversationDetail = {
  ...conversation,
  runs: [first],
  forked_from: {
    conversation_id: "00000000-0000-4000-8000-000000000099",
    run_id: "00000000-0000-4000-8000-000000000098",
    operation_id: "00000000-0000-4000-8000-000000000097",
    position: 1,
  },
};
const snapshot: Inspection["operation"] = {
  kind: "model",
  request: {
    prompt: first.prompt,
    history: [],
    previous_turns: [
      {
        prompt: "Create the original document.",
        history: [
          {
            kind: "model",
            native_context: [{ encrypted_content: "Private provider context" }],
            output: {
              status: "completed",
              text: "The original document is ready.",
              tool_calls: [],
            },
          },
        ],
      },
      {
        prompt: "Add a saved note.",
        history: [
          {
            kind: "model",
            native_context: [],
            output: {
              status: "completed",
              text: "I will write the note.",
              tool_calls: [
                {
                  call_id: "save",
                  name: "write_file",
                  arguments: { path: "note.txt", content: "ALPHA" },
                },
              ],
            },
          },
          {
            kind: "tool",
            call_id: "save",
            workspace_version_id: first.workspace_version_id,
            output: { status: "ok", value: { bytes_written: 5 } },
          },
        ],
      },
    ],
  },
};
const inspection: Inspection = {
  operation: snapshot,
  config: {
    model: "test-model",
    model_params: {},
    tool_schemas: [],
    initial_workspace_version_id: first.workspace_version_id,
    max_attempts: 5,
    max_turns: 20,
    call_timeout_seconds: 120,
    sandbox: {
      image_digest: "sha256:" + "a".repeat(64),
      workspace_max_bytes: 1000,
      log_max_bytes: 1000,
    },
  },
};
const inputPath = `**/api/runs/${first.id}/operations/${first.operations[0].id}`;
const chatPath = `**/api/conversations/${branch.id}`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route(chatPath, (route) => route.fulfill({ json: branch }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({
      json: { workspace_version_id: first.workspace_version_id, entries: [] },
    }),
  );
});

for (const width of [390, 1440]) {
  test(`shows the frozen inherited history before the branch at ${width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.install();
    await page.setViewportSize({ width, height: 1000 });
    let reads = 0;
    let sourceReads = 0;
    await page.route(`**/api/conversations/${branch.forked_from!.conversation_id}`, (route) => {
      sourceReads++;
      return route.fulfill({ json: { message: "BETA after the checkpoint" } });
    });
    await page.route(inputPath, (route) => {
      reads++;
      return route.fulfill({ json: inspection });
    });
    await page.goto(`/chats/${branch.id}`);
    const history = page.getByRole("region", { name: "Inherited history", exact: true });
    const inherited = history.getByRole("article", { name: "Inherited chat turn", exact: true });
    await expect(inherited).toHaveCount(2);
    await expect(inherited.nth(0)).toContainText("The original document is ready.");
    await expect(inherited.nth(1)).toContainText("I will write the note.");
    await history.getByText("Saved tool call · write_file", { exact: true }).click();
    await expect(history.getByText("ALPHA", { exact: true })).toBeVisible();
    await expect(page.getByText("Branched here", { exact: true })).toBeVisible();
    const current = page.getByRole("article", { name: "Chat turn", exact: true });
    await expect(current).toHaveCount(1);
    expect(
      await history.evaluate((node) =>
        Boolean(
          node.compareDocumentPosition(document.querySelector('article[aria-label="Chat turn"]')!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      ),
    ).toBe(true);
    await expect(
      history.getByText(/token usage|Retry pending|Private provider context|BETA/),
    ).toHaveCount(0);
    await expect(history.getByRole("button", { name: "Branch from here" })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/inherited-history-${width}.png`, fullPage: true });

    const loadedReads = reads;
    // Follow-ups append new turns without duplicating or refetching the immutable prefix.
    await page.route(chatPath, (route) =>
      route.fulfill({
        json: {
          ...branch,
          runs: [first, { ...runDetail, id: "new-turn", prompt: "Add a footer." }],
        },
      }),
    );
    await page.clock.fastForward(2_000);
    await expect(current).toHaveCount(2);
    await expect(inherited).toHaveCount(2);
    expect(reads).toBe(loadedReads);
    expect(sourceReads).toBe(0);
    expect(errors).toEqual([]);
  });
}

test("keeps branch messages visible when history fails and retries explicitly", async ({
  page,
}) => {
  let failing = true;
  await page.route(inputPath, (route) => {
    return route.fulfill(
      failing
        ? { status: 503, json: { error: { message: "Temporarily unavailable." } } }
        : { json: inspection },
    );
  });
  await page.goto(`/chats/${branch.id}`);
  await expect(page.getByRole("alert", { name: "Inherited history error" })).toContainText(
    "Could not load earlier messages.",
  );
  await expect(page.getByText(first.prompt, { exact: true })).toBeVisible();
  failing = false;
  await page.getByRole("button", { name: "Retry history" }).click();
  await expect(page.getByRole("article", { name: "Inherited chat turn", exact: true })).toHaveCount(
    2,
  );
  await expect(page.getByRole("alert", { name: "Inherited history error" })).toHaveCount(0);
});

test("ordinary chats do not fetch inherited history", async ({ page }) => {
  let reads = 0;
  await page.route(inputPath, (route) => {
    reads++;
    return route.abort();
  });
  await page.route(chatPath, (route) => route.fulfill({ json: { ...branch, forked_from: null } }));
  await page.goto(`/chats/${branch.id}`);
  await expect(page.getByText(first.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText("Branched here", { exact: true })).toHaveCount(0);
  expect(reads).toBe(0);
});
