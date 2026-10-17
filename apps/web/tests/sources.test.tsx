import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectForm, SyncForm } from "@/components/sources/connect-form";
import { ApiProblem, type SourceConnection } from "@/lib/api/types";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

const postJson = vi.fn();
vi.mock("@/lib/api/client", () => ({ postJson: (...args: unknown[]) => postJson(...args) }));

afterEach(cleanup);
beforeEach(() => {
  postJson.mockReset();
  push.mockReset();
});

function fill() {
  fireEvent.change(screen.getByLabelText("Host"), { target: { value: "db.example.test" } });
  fireEvent.change(screen.getByLabelText("Database"), { target: { value: "shop" } });
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "reader" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "s3cret-pw" } });
}

describe("ConnectForm", () => {
  it("shows discovered tables and preselects same-named ones", async () => {
    postJson.mockResolvedValue({ ok: true, tables: ["customers", "orders", "products", "returns", "misc"] });
    render(<ConnectForm businessId="b1" />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText(/Connection valid/);
    expect(screen.getByRole("list", { name: "Discovered tables" }).textContent).toContain("misc");
    expect((screen.getByLabelText("orders table") as HTMLSelectElement).value).toBe("orders");
    const [path, body] = postJson.mock.calls[0]!;
    expect(path).toBe("/api/v1/businesses/b1/sources/test");
    expect(body).toMatchObject({ host: "db.example.test", port: 5432, schema: "public", password: "s3cret-pw" });
  });

  it("shows the problem detail without the password and clears the field", async () => {
    postJson.mockRejectedValue(
      new ApiProblem({ type: "about:blank", title: "Bad", status: 422, detail: "Could not sign in to the database.", code: "source_auth_failed", request_id: null }),
    );
    const { container } = render(<ConnectForm businessId="b1" />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Could not sign in to the database.");
    expect(container.innerHTML).not.toContain("s3cret-pw");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
  });
});

describe("SyncForm", () => {
  const connection: SourceConnection = {
    id: "c1", kind: "postgres", host: "h", port: 5432, database: "shop", username: "u", schema_name: "public",
    table_orders: "orders", table_products: "products", table_customers: "customers", table_returns: "returns",
    created_at: "2026-01-01T00:00:00Z", last_sync_at: null, last_sync_status: null, last_sync_error: null, last_dataset_id: null,
  };

  it("clears the password and opens the new dataset", async () => {
    postJson.mockResolvedValue({ dataset_id: "d9", status: "queued" });
    render(<SyncForm businessId="b1" connection={connection} />);
    const input = screen.getByLabelText("Password") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(input.value).toBe("");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/businesses/b1/datasets/d9"));
    expect(postJson).toHaveBeenCalledTimes(1);
  });
});
