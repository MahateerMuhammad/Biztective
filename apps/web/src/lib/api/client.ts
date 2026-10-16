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

async function send(path: string, body: unknown, token: string): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function postJson<T>(path: string, body?: unknown): Promise<T | null> {
  let response = await send(path, body, csrfToken ?? (await fetchCsrfToken()));
  if (response.status === 403) {
    const problem = await toApiProblem(response.clone());
    if (problem.code === "csrf_failed") {
      response = await send(path, body, await fetchCsrfToken());
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
