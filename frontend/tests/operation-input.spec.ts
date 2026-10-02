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
          output: { status: "completed", text: "Checking the calculator.", tool_calls: [] },
          native_context: [{ type: "reasoning", encrypted_content: "opaque-blob" }],
        },
      ],
      previous_turns: [
        {
          prompt: "Create a calculator.",
          history: [
            {
              kind: "tool",
              call_id: "read_earlier",
              workspace_version_id: runDetail.workspace_version_id,
              output: { status: "ok", value: { content: "Earlier file contents" } },
            },
          ],
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
    max_steps: 100,
    call_timeout_seconds: 120,
    sandbox: { image_digest: "sha256:test", workspace_max_bytes: 1000, log_max_bytes: 1000 },
  },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/runs/*/files?**", (route) =>
    route.fulfill({ json: { workspace_version_id: runDetail.workspace_version_id, entries: [] } }),
  );
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        prompt: "Fix the failing test.",
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

test("loads context lazily without repeating the current prompt or exposing provider data", async ({
  page,
}) => {
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
  await page.getByRole("tab", { name: "Output", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByText("Fix the failing test.", { exact: true })).toBeVisible();
  await expect(page.getByText("Fix the failing test.", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "Output", exact: true })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  await expect(page.getByText("Checking the calculator.", { exact: true })).toBeVisible();
  await expect(page.getByText("Earlier file contents", { exact: true })).toBeVisible();
  await expect(page.getByText("Create a calculator.", { exact: true })).toBeVisible();
  await expect(page.getByText("This turn failed: Interrupted")).toBeVisible();
  await expect(page.getByText(/Encrypted context present/)).toBeHidden();
  await page.getByRole("tab", { name: "Technical details", exact: true }).click();
  await page.getByText("Raw saved context", { exact: true }).click();
  await expect(page.getByText(/Encrypted context present/)).toBeVisible();
  await expect(page.getByText("opaque-blob", { exact: false })).toHaveCount(0);
  await page.clock.fastForward(4000);
  expect(calls).toBe(1);
});

for (const failure of ["malformed", "invalid explicit input", "unavailable"]) {
  test(`shows loading and ${failure} responses, and retries on demand`, async ({ page }) => {
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
            : failure === "invalid explicit input"
              ? {
                  json: {
                    ...input,
                    operation: {
                      kind: "model",
                      request: { prompt: "", history: [], explicit_input: {} },
                    },
                  },
                }
              : { status: 503, json: { error: { message: "Inspection unavailable" } } },
      );
    });
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    await page.getByLabel("1. Model call", { exact: true }).locator(":scope > summary").click();
    const trigger = page.getByRole("tab", { name: "Context", exact: true });
    await trigger.click();
    await expect(page.getByText("Loading saved input…")).toBeVisible();
    release();
    await expect(page.getByRole("alert", { name: "Input inspection error" })).toContainText(
      failure === "malformed"
        ? "The saved input response is invalid."
        : failure === "invalid explicit input"
          ? "The saved model input is invalid."
          : "Inspection unavailable",
    );
    await page.getByRole("button", { name: "Retry inspection" }).click();
    await expect(page.getByText("Checking the calculator.", { exact: true })).toBeVisible();
    expect(calls).toBe(2);
  });
}

for (const name of ["builtin", "reviewer"]) {
  test(`inspects frozen ${name} model input and request overrides without legacy context`, async ({
    page,
  }) => {
    const explicitInput = [
      { role: "developer", content: `You are the ${name} agent.` },
      { role: "user", content: "Fix the failing test." },
      {
        type: "function_call",
        call_id: "read_1",
        name: "read_file",
        arguments: '{"path":"test.py"}',
      },
      { type: "function_call_output", call_id: "read_1", output: "Saved file contents" },
    ];
    const schemas = name === "builtin" ? [{ type: "function", name: "write_file" }] : [];
    let calls = 0;
    await page.route(endpoint, (route) => {
      calls++;
      return route.fulfill({
        json: {
          ...input,
          config: {
            ...input.config,
            agent: { name, version: "1", app_ref: "example.app:app", build_id: "build-1" },
          },
          operation: {
            kind: "model",
            request: {
              prompt: "",
              history: [],
              explicit_input: explicitInput,
              model: "call-model",
              model_params: { temperature: 0 },
              tool_schemas: schemas,
            },
          },
        },
      });
    });
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const step = page.getByLabel("1. Model call", { exact: true });
    await step.locator(":scope > summary").click();
    expect(calls).toBe(0);
    await step.getByRole("tab", { name: "Context", exact: true }).click();
    await expect(step.getByRole("heading", { name: "Saved model input" })).toBeVisible();
    await expect(step.getByRole("tabpanel").locator("pre")).toHaveText(
      JSON.stringify(explicitInput, null, 2),
    );
    await expect(step).not.toContainText("No earlier results.");
    await step.getByRole("tab", { name: "Technical details", exact: true }).click();
    await step.getByText("Raw saved context", { exact: true }).click();
    await expect(
      step.getByText("Raw saved context", { exact: true }).locator("..").locator("pre"),
    ).toHaveText(JSON.stringify(explicitInput, null, 2));
    await expect(step).not.toContainText("Provider input is assembled");
    const config = step.getByText("Configuration, tool schemas, and limits", { exact: true });
    await config.click();
    const settings = JSON.parse((await config.locator("..").locator("pre").textContent())!);
    expect(settings.model).toBe("call-model");
    expect(settings.model_params).toEqual({ temperature: 0 });
    expect(settings.tool_schemas).toEqual(schemas);
    expect(settings.max_attempts).toBe(input.config.max_attempts);
    expect(calls).toBe(1);
  });
}

for (const empty of [true, false]) {
  test(`${empty ? "empty" : "null"} model request fields ${empty ? "override" : "fall back to"} legacy values`, async ({
    page,
  }) => {
    await page.route(endpoint, (route) =>
      route.fulfill({
        json: {
          ...input,
          operation: {
            kind: "model",
            request: {
              ...input.operation.request,
              explicit_input: empty ? [] : null,
              model: null,
              model_params: empty ? {} : null,
              tool_schemas: empty ? [] : null,
            },
          },
        },
      }),
    );
    await page.goto(`/runs/${runDetail.id}`);
    await page.getByLabel("Activity", { exact: true }).locator(":scope > summary").click();
    const step = page.getByLabel("1. Model call", { exact: true });
    await step.locator(":scope > summary").click();
    await step.getByRole("tab", { name: "Context", exact: true }).click();
    if (empty) {
      await expect(step.getByRole("tabpanel").locator("pre")).toHaveText("[]");
      await expect(step).not.toContainText("Checking the calculator.");
    } else {
      await expect(step.getByText("Checking the calculator.", { exact: true })).toBeVisible();
      await expect(step.getByText("Earlier file contents", { exact: true })).toBeVisible();
    }
    await step.getByRole("tab", { name: "Technical details", exact: true }).click();
    const config = step.getByText("Configuration, tool schemas, and limits", { exact: true });
    await config.click();
    expect(JSON.parse((await config.locator("..").locator("pre").textContent())!)).toEqual({
      ...input.config,
      model_params: empty ? {} : input.config.model_params,
      tool_schemas: empty ? [] : input.config.tool_schemas,
    });
  });
}

test("shows full tool arguments without executing their contents", async ({ page }) => {
  await page.route(`**/api/runs/${runDetail.id}`, (route) =>
    route.fulfill({
      json: {
        ...runDetail,
        prompt: "Fix the failing test.",
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
                content: "<script>not executable</script>\nSecond line",
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
  await page.getByRole("tab", { name: "Arguments", exact: true }).click();
  await expect(page.getByRole("region", { name: "Execution history" })).toContainText(
    "<script>not executable</script>",
  );
  await expect(
    page.getByLabel("1. write_file").locator("pre").filter({ hasText: "Second line" }),
  ).toHaveText("<script>not executable</script>\nSecond line");
  const workspace = page.getByText(`Input workspace: ${runDetail.workspace_version_id}`, {
    exact: false,
  });
  await expect(workspace).toBeHidden();
  await page.getByRole("tab", { name: "Technical details", exact: true }).click();
  await expect(workspace).toBeVisible();
});

for (const close of ["Output", "Timeline", "step", "Activity", "navigation"]) {
  test(`cancels inspection on ${close} and can load it again`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    await page.route(endpoint, async (route) => {
      calls++;
      if (calls === 1) await pending;
      await route.fulfill({ json: input });
    });
    await page.goto(`/runs/${runDetail.id}`);
    const activity = page.getByLabel("Activity", { exact: true }).locator(":scope > summary");
    const step = page.getByLabel("1. Model call", { exact: true }).locator(":scope > summary");
    await activity.click();
    await step.click();
    expect(calls).toBe(0);
    await page.getByRole("tab", { name: "Context", exact: true }).click();
    await expect(page.getByText("Loading saved input…")).toBeVisible();
    const aborted = page.waitForEvent("requestfailed", (request) =>
      request.url().endsWith(`/operations/${operationId}`),
    );
    if (close === "Output" || close === "Timeline")
      await page.getByRole("tab", { name: close, exact: true }).click();
    else if (close === "step") await step.click();
    else if (close === "Activity") await activity.click();
    else await page.getByRole("link", { name: "New chat", exact: true }).click();
    await aborted;
    release();
    if (close === "navigation") await page.goto(`/runs/${runDetail.id}`);
    if (close === "Activity" || close === "navigation") await activity.click();
    if (close !== "Output" && close !== "Timeline") await step.click();
    await page.getByRole("tab", { name: "Context", exact: true }).click();
    await expect(page.getByText("Checking the calculator.", { exact: true })).toBeVisible();
    expect(calls).toBe(2);
  });
}
