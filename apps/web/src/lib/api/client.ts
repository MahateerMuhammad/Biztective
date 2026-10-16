/**
 * Browser-side API calls through the same-origin /api/v1 rewrite.
 * Unsafe requests carry the session-bound CSRF token (AMD-4); a token rejected because the
 * session changed (e.g. after login) is refreshed once and the request retried.
 */
import { toApiProblem } from "./types";

let csrfToken: string | null = null;

async function fetchCsrfToken(): Promise<string> {
  const response = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
  if (!response.ok) {
    throw await toApiProblem(response);
  }
  const body = (await response.json()) as { csrf_token: string };
  csrfToken = body.csrf_token;
  return csrfToken;
}

/** Forget the cached token, e.g. after the session changed. */
export function resetCsrfToken(): void {
  csrfToken = null;
}

type Body = { json: unknown } | { form: FormData } | undefined;

async function send(
  path: string,
  body: Body,
  token: string,
  extraHeaders: Record<string, string>,
): Promise<Response> {
  const headers: Record<string, string> = { "X-CSRF-Token": token, ...extraHeaders };
  let payload: BodyInit | undefined;
  if (body && "json" in body) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body.json);
  } else if (body) {
    payload = body.form; // the browser sets the multipart boundary
  }
  return fetch(path, { method: "POST", credentials: "same-origin", headers, body: payload });
}

async function post<T>(
  path: string,
  body: Body,
  extraHeaders: Record<string, string> = {},
): Promise<T | null> {
  let response = await send(path, body, csrfToken ?? (await fetchCsrfToken()), extraHeaders);
  if (response.status === 403) {
    const problem = await toApiProblem(response.clone());
    if (problem.code === "csrf_failed") {
      response = await send(path, body, await fetchCsrfToken(), extraHeaders);
    }
  }
  if (!response.ok) {
    throw await toApiProblem(response);
  }
  if (response.status === 204) {
    return null;
  }
  return (await response.json()) as T;
}

/** JSON POST. Pass the same idempotency key when retrying the same submission. */
export async function postJson<T>(path: string, body?: unknown, idempotencyKey?: string): Promise<T | null> {
  return post<T>(
    path,
    body === undefined ? undefined : { json: body },
    idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
  );
}

/**
 * Multipart upload. Pass the same idempotency key when retrying the same submission so the
 * server never creates a second dataset version for it.
 */
export async function postForm<T>(path: string, form: FormData, idempotencyKey: string): Promise<T> {
  const result = await post<T>(path, { form }, { "Idempotency-Key": idempotencyKey });
  if (result === null) {
    throw new Error("Unexpected empty response");
  }
  return result;
}
