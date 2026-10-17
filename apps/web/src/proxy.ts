import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE_NAMES } from "@/lib/session-cookie";

const PUBLIC_PATHS = new Set(["/login"]);

function contentSecurityPolicy(nonce: string): string {
  const isDev = process.env.NODE_ENV === "development";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    // Next.js and UI primitives emit inline style attributes (SECURITY_MODEL §7 residual risk).
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

function backendOrigin(): string {
  const value = process.env.BACKEND_URL;
  if (!value) {
    throw new Error("BACKEND_URL must be set (e.g. http://127.0.0.1:8000)");
  }
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("BACKEND_URL must be an http(s) URL");
  }
  return url.origin;
}

/**
 * Runtime /api/v1/* rewrite to BACKEND_URL (so container runtime env overrides build-time
 * routes-manifest), per-request CSP nonce, plus an optimistic redirect for visitors without
 * a session cookie. The backend remains the authority: pages re-validate the session on every render.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (pathname.startsWith("/api/v1/")) {
    return NextResponse.rewrite(
      new URL(`${pathname}${request.nextUrl.search}`, backendOrigin()),
    );
  }
  if (pathname.startsWith("/api/")) {
    return NextResponse.next();
  }
  const hasSessionCookie = SESSION_COOKIE_NAMES.some((name) => request.cookies.has(name));
  if (!hasSessionCookie && !PUBLIC_PATHS.has(pathname)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = contentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
