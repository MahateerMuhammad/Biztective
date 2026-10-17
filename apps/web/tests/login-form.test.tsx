import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm, loginErrorMessage } from "@/app/login/login-form";
import { LogoutButton } from "@/components/logout-button";
import { ApiProblem } from "@/lib/api/types";

const replace = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));

const postJson = vi.fn();
vi.mock("@/lib/api/client", () => ({
  postJson: (...args: unknown[]) => postJson(...args),
  resetCsrfToken: vi.fn(),
}));

// jsdom has no IntersectionObserver (used by the reveal animation); report everything as visible.
class VisibleObserver {
  constructor(private readonly callback: IntersectionObserverCallback) {}
  observe(target: Element) {
    this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as never);
  }
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("IntersectionObserver", VisibleObserver);
// ...nor matchMedia (used by the theme toggle).
vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => {},
  removeEventListener: () => {},
}));

function apiProblem(status: number, code: string, retryAfter: number | null = null): ApiProblem {
  return new ApiProblem(
    { type: "t", title: "t", status, detail: "d", code, request_id: null },
    retryAfter,
  );
}

async function submit(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  beforeEach(() => {
    replace.mockReset();
    refresh.mockReset();
    postJson.mockReset();
  });
  afterEach(cleanup);

  it("has labelled fields and a heading", () => {
    render(<LoginForm />);
    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeDefined();
    expect(screen.getByLabelText("Email").getAttribute("autocomplete")).toBe("username");
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("password");
  });

  it("posts credentials and navigates on success", async () => {
    postJson.mockResolvedValueOnce({ id: "u", email: "a@b.c", display_name: "A" });
    render(<LoginForm />);
    await submit("a@b.c", "secret password");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(postJson).toHaveBeenCalledWith("/api/v1/auth/login", {
      email: "a@b.c",
      password: "secret password",
    });
    expect(refresh).toHaveBeenCalled();
  });

  it("shows an accessible error and re-enables the button on failure", async () => {
    postJson.mockRejectedValueOnce(apiProblem(401, "invalid_credentials"));
    render(<LoginForm />);
    await submit("a@b.c", "wrong but long enough");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Invalid email or password.");
    expect(screen.getByLabelText("Email").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveProperty("disabled", false);
    expect(replace).not.toHaveBeenCalled();
  });

  it("validates fields inline without sending a request", async () => {
    render(<LoginForm />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Email is required.")).toBeDefined();
    expect(screen.getByText("Password is required.")).toBeDefined();
    expect(document.activeElement).toBe(screen.getByLabelText("Email"));

    await submit("not-an-email", "short");
    expect(await screen.findByText("Enter a valid email address.")).toBeDefined();
    expect(screen.getByText("Password must be at least 12 characters.")).toBeDefined();
    expect(screen.getByLabelText("Password").getAttribute("aria-describedby")).toBe("password-error");
    expect(postJson).not.toHaveBeenCalled();

    // After a first submit, leaving a field re-checks it.
    const emailField = screen.getByLabelText("Email");
    fireEvent.change(emailField, { target: { value: "a@b.c" } });
    fireEvent.blur(emailField);
    await waitFor(() => expect(screen.queryByText("Enter a valid email address.")).toBeNull());
  });

  it("shows and hides the password", () => {
    render(<LoginForm />);
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("password");
  });

  it("posts the form, never a GET with credentials in the URL", () => {
    render(<LoginForm />);
    expect(screen.getByLabelText("Email").closest("form")?.getAttribute("method")).toBe("post");
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveProperty("disabled", false);
  });
});

describe("loginErrorMessage", () => {
  it.each([
    [apiProblem(401, "invalid_credentials"), "Invalid email or password."],
    [apiProblem(429, "rate_limited", 61), "Too many sign-in attempts. Try again in 2 minutes."],
    [apiProblem(429, "rate_limited", 30), "Too many sign-in attempts. Try again in 1 minute."],
    [apiProblem(429, "rate_limited"), "Too many sign-in attempts. Try again in 15 minutes."],
    [apiProblem(422, "validation_error"), "Enter a valid email address and password."],
    [apiProblem(500, "internal_error"), "Sign-in failed. Please try again."],
    [new TypeError("network"), "Sign-in failed. Please try again."],
  ])("maps %o", (error, message) => {
    expect(loginErrorMessage(error)).toBe(message);
  });
});

describe("LogoutButton", () => {
  afterEach(cleanup);

  beforeEach(() => {
    replace.mockReset();
    postJson.mockReset();
  });

  it("signs out and returns to the login page", async () => {
    postJson.mockResolvedValueOnce(null);
    render(<LogoutButton />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(postJson).toHaveBeenCalledWith("/api/v1/auth/logout");
  });

  it("reports a failed sign-out instead of navigating away", async () => {
    postJson.mockRejectedValueOnce(apiProblem(503, "unavailable"));
    render(<LogoutButton />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Sign-out failed. Try again.");
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toHaveProperty("disabled", false);
  });
});
