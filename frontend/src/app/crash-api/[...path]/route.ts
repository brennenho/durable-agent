import { readFile } from "node:fs/promises";
import { NextRequest, NextResponse } from "next/server";

type Context = { params: Promise<{ path: string[] }> };
const routes = new Set([
  "GET snapshot",
  "POST scenarios",
  "POST scenarios/cancel",
  "POST containers/action",
]);

async function proxy(request: NextRequest, context: Context) {
  const path = (await context.params).path.join("/");
  if (!routes.has(`${request.method} ${path}`)) return new NextResponse(null, { status: 404 });
  // Docker can publish a different port than Next listens on. Compare the browser's
  // origin to the actual Host, restricted to loopback, rather than Next's internal URL.
  const host = request.headers.get("host") ?? "";
  const localHost = /^(127\.0\.0\.1|localhost)(:\d{1,5})?$/.test(host);
  if (
    request.method === "POST" &&
    (!localHost || request.headers.get("origin") !== `${request.nextUrl.protocol}//${host}`)
  ) {
    return NextResponse.json({ detail: "Same-origin request required" }, { status: 403 });
  }
  const url = process.env.CRASH_TEST_URL;
  const tokenFile = process.env.CRASH_TEST_TOKEN_FILE;
  if (!url || !tokenFile) {
    return NextResponse.json(
      { detail: "Start Crashboard with scripts/crash-test.sh." },
      { status: 503 },
    );
  }
  try {
    const token = (await readFile(tokenFile, "utf8")).trim();
    const response = await fetch(`${url}/${path}`, {
      method: request.method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: request.method === "POST" ? await request.text() : undefined,
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
      redirect: "error",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { detail: "Crash Test controller is unavailable. Last observations are retained." },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
