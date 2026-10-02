import { expect, test } from "@playwright/test";

test("controller proxy rejects unknown paths and cross-origin mutations", async ({ request }) => {
  expect((await request.get("/crash-api/hooks")).status()).toBe(404);
  expect(
    (
      await request.post("/crash-api/containers/action", {
        headers: { Origin: "http://unrelated.test" },
        data: {},
      })
    ).status(),
  ).toBe(403);
  expect((await request.post("/crash-api/scenarios", { data: {} })).status()).toBe(403);
  expect(
    (
      await request.post("/crash-api/scenarios", {
        headers: { Host: "unrelated.test", Origin: "http://unrelated.test" },
        data: {},
      })
    ).status(),
  ).toBe(403);
  const response = await request.get("/crash-api/snapshot");
  expect(response.status()).toBe(503);
  expect(await response.json()).toEqual({
    detail: "Start the host dashboard with scripts/crash-test.sh.",
  });
});

test("same-origin loopback mutations reach the controller configuration check", async ({
  request,
  baseURL,
}) => {
  for (const hostname of ["127.0.0.1", "localhost"]) {
    const origin = new URL(baseURL!);
    origin.hostname = hostname;
    const response = await request.post("/crash-api/scenarios", {
      headers: { Host: origin.host, Origin: origin.origin },
      data: { scenario: "baseline" },
    });
    expect(response.status()).toBe(503);
    expect(await response.json()).toEqual({
      detail: "Start the host dashboard with scripts/crash-test.sh.",
    });
  }
  const otherOrigin = new URL(baseURL!);
  otherOrigin.hostname = "localhost";
  expect(
    (
      await request.post("/crash-api/scenarios", {
        headers: { Origin: otherOrigin.origin },
        data: { scenario: "baseline" },
      })
    ).status(),
  ).toBe(403);
});
