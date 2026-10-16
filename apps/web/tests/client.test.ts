import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { postForm, postJson, resetCsrfToken } from "@/lib/api/client";
import { ApiProblem, toApiProblem } from "@/lib/api/types";

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const problem = (status: number, code: string, detail = "nope") => ({
  type: `urn:root:problem:${code}`,
  title: "t",
  status,
  detail,
  instance: "/x",
  code,
  request_id: "req-1",
});

describe("postJson", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    resetCsrfToken();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("fetches a CSRF token once and sends it on unsafe requests", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { csrf_token: "tok-1" }))
      .mockResolvedValueOnce(json(200, { ok: 1 }))
      .mockResolvedValueOnce(json(200, { ok: 2 }));

    await expect(postJson("/api/v1/a", { x: 1 })).resolves.toEqual({ ok: 1 });
    await expect(postJson("/api/v1/b")).resolves.toEqual({ ok: 2 });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [, firstPost] = fetchMock.mock.calls[1]!;
    expect(firstPost?.method).toBe("POST");
    expect(firstPost?.credentials).toBe("same-origin");
    expect((firstPost?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok-1");
    expect(firstPost?.body).toBe(JSON.stringify({ x: 1 }));
    expect(fetchMock.mock.calls[2]![1]?.body).toBeUndefined();
  });

  it("refreshes the token once when the server reports csrf_failed", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { csrf_token: "stale" }))
      .mockResolvedValueOnce(json(403, problem(403, "csrf_failed")))
      .mockResolvedValueOnce(json(200, { csrf_token: "fresh" }))
      .mockResolvedValueOnce(json(204, null));

    await expect(postJson("/api/v1/auth/logout")).resolves.toBeNull();
    const retried = fetchMock.mock.calls[3]![1]?.headers as Record<string, string>;
    expect(retried["X-CSRF-Token"]).toBe("fresh");
  });

  it("does not retry other 403 responses", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(json(403, problem(403, "forbidden", "No permission.")));

    const error = await postJson("/api/v1/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiProblem);
    expect((error as ApiProblem).code).toBe("forbidden");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("sends multipart uploads with the idempotency key and no JSON content type", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(json(202, { id: "d1" }));
    const form = new FormData();
    form.set("orders", new Blob(["a,b\n"]), "orders.csv");
    await expect(postForm("/api/v1/x", form, "key-123456789")).resolves.toEqual({ id: "d1" });
    const init = fetchMock.mock.calls[1]![1]!;
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("key-123456789");
    expect(headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBe(form);
  });

  it("rejects an empty upload response", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(json(204, null));
    await expect(postForm("/api/v1/x", new FormData(), "key-123456789")).rejects.toThrow();
  });

  it("throws a problem when the CSRF endpoint fails", async () => {
    fetchMock.mockResolvedValueOnce(json(503, problem(503, "unavailable")));
    await expect(postJson("/api/v1/x")).rejects.toMatchObject({ status: 503 });
  });
});

describe("toApiProblem", () => {
  it("parses problem details and Retry-After", async () => {
    const result = await toApiProblem(
      json(429, problem(429, "rate_limited", "Slow down."), { "Retry-After": "120" }),
    );
    expect(result).toMatchObject({
      status: 429,
      code: "rate_limited",
      message: "Slow down.",
      requestId: "req-1",
      retryAfterSeconds: 120,
      details: [],
    });
  });

  it("exposes field errors as readable details", async () => {
    const body = { ...problem(422, "validation_error"), errors: [{ field: "email", code: "x", message: "Bad." }, { field: "", code: "y", message: "Whole request." }] };
    const result = await toApiProblem(json(422, body));
    expect(result.details).toEqual(["email: Bad.", "Whole request."]);
  });

  it("falls back to a generic problem for non-JSON bodies", async () => {
    const response = new Response("<html>bad gateway</html>", {
      status: 502,
      statusText: "Bad Gateway",
      headers: { "X-Request-ID": "req-9" },
    });
    const result = await toApiProblem(response);
    expect(result).toMatchObject({ status: 502, code: "unexpected_response", requestId: "req-9" });
    expect(result.message).not.toContain("html");
    expect(result.retryAfterSeconds).toBeNull();
  });
});
