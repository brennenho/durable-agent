import { expect, test } from "@playwright/test";
import type { components } from "../src/lib/api.generated";
import { runDetail } from "./fixtures";

type Inspection = components["schemas"]["OperationInspection"];
const operationId = "00000000-0000-4000-8000-000000000010";
const endpoint = `**/api/runs/${runDetail.id}/operations/${operationId}`;
const input: Inspection = {
  operation: {
    kind: "model",
    request: {
      prompt: "Fix the failing test.",
      history: [
        {
          kind: "model",
          output: { status: "completed", text: "", tool_calls: [] },
          native_context: [{ type: "reasoning", encrypted_content: "opaque-blob" }],
        },
      ],
      previous_turns: [
        {
          prompt: "Create a calculator.",
          history: [],
          terminal_error: { code: "interrupted", message: "Interrupted", retryable: false },
        },
      ],
    },
  },
  config: {
    model: "test-model",
    model_params: { max_output_tokens: 100 },
    tool_schemas: [{ type: "function", name: "run_command" }],
    initial_workspace_version_id: runDetail.workspace_version_id,
    max_attempts: 5,
    max_turns: 20,
    call_timeout_seconds: 120,
    sandbox: { image_digest: "sha256:test", workspace_max_bytes: 1000, log_max_bytes: 1000 },
  },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files", (route) =>
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        operations: [
          {
            id: operationId,
            kind: "model",
            position: 0,
            created_at: runDetail.created_at,
            completed_at: null,
            result: null,
            attempts: [],
          },
        ],
      },
    }),
  );
});

test("loads saved model input only on expansion and keeps context collapsed", async ({ page }) => {
  await page.clock.install();
  let calls = 0;
  await page.route(endpoint, (route) => {
    calls++;
    return route.fulfill({ json: input });
  });
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.clock.fastForward(4000);
  expect(calls).toBe(0);
  await page.getByLabel("1. Model call", { exact: true }).locator(":scope > summary").click();
  await page.getByText("Saved model input", { exact: true }).click();
  await expect(page.getByText("Fix the failing test.", { exact: true })).toBeVisible();
  await expect(page.getByText("Requested model: test-model")).toBeVisible();
  await expect(page.getByText("Create a calculator.", { exact: true })).toBeHidden();
  await page.getByText("Previous turns (1)", { exact: true }).click();
  await expect(page.getByText("Create a calculator.", { exact: true })).toBeVisible();
  await expect(page.getByText(/The worker adds interruption records/)).toBeVisible();
  await page.getByText("Current turn history (1 results)", { exact: true }).click();
  await expect(page.getByText(/Encrypted context present/)).toBeVisible();
  await expect(page.getByText("opaque-blob", { exact: false })).toHaveCount(0);
  await page.clock.fastForward(4000);
  expect(calls).toBe(1);
});

for (const failure of ["malformed", "unavailable"]) {
  test(`shows loading and ${failure} responses, and retries when reopened`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    await page.route(endpoint, async (route) => {
      calls++;
      await pending;
      await route.fulfill(
        calls > 1
          ? { json: input }
          : failure === "malformed"
            ? { json: {} }
            : { status: 503, json: { error: { message: "Inspection unavailable" } } },
      );
    });
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    await page.getByLabel("1. Model call", { exact: true }).locator(":scope > summary").click();
    const trigger = page.getByText("Saved model input", { exact: true });
    await trigger.click();
    await expect(page.getByText("Loading saved input…")).toBeVisible();
    release();
    await expect(page.getByRole("alert", { name: "Input inspection error" })).toContainText(
      failure === "malformed" ? "The saved input response is invalid." : "Inspection unavailable",
    );
    await trigger.click();
    await trigger.click();
    await expect(page.getByText("Fix the failing test.", { exact: true })).toBeVisible();
    expect(calls).toBe(2);
  });
}

test("shows full tool arguments without executing their contents", async ({ page }) => {
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        operations: [
          {
            id: operationId,
            kind: "tool",
            tool_name: "write_file",
            position: 0,
            created_at: runDetail.created_at,
            completed_at: null,
            result: null,
            attempts: [],
          },
        ],
      },
    }),
  );
  await page.route(endpoint, (route) =>
    route.fulfill({
      json: {
        ...input,
        operation: {
          kind: "tool",
          request: {
            call: {
              call_id: "call_write",
              name: "write_file",
              arguments: {
                path: "report.md",
                content: "<script>not executable</script>",
              },
            },
            input_version_id: runDetail.workspace_version_id,
          },
        },
      },
    }),
  );
  await page.goto(`/runs/${runDetail.id}`);
  await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
  await page.getByLabel("1. write_file", { exact: true }).locator(":scope > summary").click();
  await page.getByText("Saved tool input", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Execution history" })).toContainText(
    "<script>not executable</script>",
  );
  await expect(page.getByText(`Input workspace: ${runDetail.workspace_version_id}`)).toBeVisible();
});
