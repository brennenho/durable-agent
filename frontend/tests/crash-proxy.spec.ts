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
    (await request.post("/crash-api/autoscaling", { data: { enabled: false } })).status(),
  ).toBe(403);
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
    detail: "Start Crashboard with scripts/crash-test.sh.",
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
      detail: "Start Crashboard with scripts/crash-test.sh.",
    });
    expect(
      (
        await request.post("/crash-api/autoscaling", {
          headers: { Host: origin.host, Origin: origin.origin },
          data: { enabled: false },
        })
      ).status(),
    ).toBe(503);
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

test("published Docker ports preserve same-origin checks", async ({ request }) => {
  const post = (host: string, origin: string) =>
    request.post("/crash-api/scenarios", {
      headers: { Host: host, Origin: origin },
      data: { scenario: "baseline" },
    });
  expect((await post("127.0.0.1:3020", "http://127.0.0.1:3020")).status()).toBe(503);
  expect((await post("127.0.0.1:3020", "http://127.0.0.1:3021")).status()).toBe(403);
  expect(
    (await post("localhost.attacker.test:3020", "http://localhost.attacker.test:3020")).status(),
  ).toBe(403);
});
