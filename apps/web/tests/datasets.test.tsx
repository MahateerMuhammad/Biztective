import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MappingReview,
  pendingColumns,
  type ReviewFile,
} from "@/app/(app)/businesses/[businessId]/datasets/[datasetId]/mapping-review";
import { UploadForm, uploadErrorFrom } from "@/app/(app)/businesses/[businessId]/datasets/new/upload-form";
import { ApiProblem } from "@/lib/api/types";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));

const postForm = vi.fn();
const postJson = vi.fn();
vi.mock("@/lib/api/client", () => ({
  postForm: (...args: unknown[]) => postForm(...args),
  postJson: (...args: unknown[]) => postJson(...args),
}));

function problem(status: number, code: string, detail: string, errors: { field: string; message: string }[] = []) {
  return new ApiProblem({
    type: "t",
    title: "t",
    status,
    detail,
    code,
    request_id: null,
    errors: errors.map((e) => ({ ...e, code })),
  });
}

beforeEach(() => {
  push.mockReset();
  refresh.mockReset();
  postForm.mockReset();
  postJson.mockReset();
});
afterEach(cleanup);

describe("UploadForm", () => {
  it("labels every file input and explains the time zone", () => {
    render(<UploadForm businessId="b1" defaultTimezone="Asia/Karachi" currency="PKR" />);
    for (const label of ["Orders (.csv)", "Products (.csv)", "Customers (.csv)", "Returns (.csv)"]) {
      expect(screen.getByLabelText(label).getAttribute("accept")).toBe(".csv,text/csv");
    }
    expect((screen.getByLabelText("Time zone of times in the files") as HTMLInputElement).value).toBe(
      "Asia/Karachi",
    );
    expect(screen.getByText(/Amounts must be in PKR/)).toBeDefined();
  });

  it("navigates to the new dataset on success", async () => {
    postForm.mockResolvedValueOnce({ id: "d1" });
    render(<UploadForm businessId="b1" defaultTimezone="UTC" currency="USD" />);
    fireEvent.click(screen.getByRole("button", { name: "Upload and validate" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/businesses/b1/datasets/d1"));
    const [path, form, key] = postForm.mock.calls[0]!;
    expect(path).toBe("/api/v1/businesses/b1/datasets");
    expect(form).toBeInstanceOf(FormData);
    expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it("shows server explanations and reuses the idempotency key on retry", async () => {
    postForm
      .mockRejectedValueOnce(
        problem(422, "missing_required_columns", "orders.csv is missing required columns: quantity.", [
          { field: "orders.quantity", message: "required column not found (accepted headers: quantity, qty)" },
        ]),
      )
      .mockRejectedValueOnce(new TypeError("network"));
    render(<UploadForm businessId="b1" defaultTimezone="UTC" currency="USD" />);
    fireEvent.click(screen.getByRole("button", { name: "Upload and validate" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("missing required columns: quantity");
    expect(alert.textContent).toContain("orders.quantity: required column not found");
    fireEvent.click(screen.getByRole("button", { name: "Upload and validate" }));
    await waitFor(() => expect(postForm).toHaveBeenCalledTimes(2));
    expect(postForm.mock.calls[1]![2]).toBe(postForm.mock.calls[0]![2]);
    expect((await screen.findByRole("alert")).textContent).toContain("Upload failed");
  });

  it("maps unknown errors to a generic message", () => {
    expect(uploadErrorFrom(new Error("x")).message).toMatch(/Upload failed/);
  });
});

const files: ReviewFile[] = [
  {
    kind: "orders",
    headers: ["order_line_id", "qty", "date", "created_at"],
    mapping: {
      needs_confirmation: true,
      ignored: [],
      pii_dropped: ["customer_email"],
      columns: {
        order_line_id: { source: "order_line_id", status: "exact", candidates: [], required: true },
        quantity: { source: "qty", status: "proposed", candidates: ["qty"], required: true },
        order_date: { source: null, status: "ambiguous", candidates: ["date", "created_at"], required: true },
        channel: { source: null, status: "unmapped", candidates: [], required: false },
      },
    },
  },
  {
    kind: "products",
    headers: ["product_id"],
    mapping: {
      needs_confirmation: false,
      ignored: [],
      pii_dropped: [],
      columns: { product_id: { source: "product_id", status: "exact", candidates: [], required: true } },
    },
  },
];

describe("MappingReview", () => {
  it("lists only columns that need a human decision", () => {
    expect(pendingColumns(files[0]!.mapping)).toEqual(["quantity", "order_date"]);
    render(<MappingReview businessId="b1" datasetId="d1" files={files} />);
    expect(screen.getByRole("group", { name: "orders.csv" })).toBeDefined();
    expect(screen.queryByRole("group", { name: "products.csv" })).toBeNull();
    expect(screen.getByText(/Several columns could match: date, created_at/)).toBeDefined();
  });

  it("submits explicit choices, with undecided required columns sent as null", async () => {
    postJson.mockResolvedValueOnce({});
    render(<MappingReview businessId="b1" datasetId="d1" files={files} />);
    fireEvent.change(screen.getByLabelText(/order_date/), { target: { value: "date" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm mapping and validate" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(postJson).toHaveBeenCalledWith("/api/v1/businesses/b1/datasets/d1/mapping", {
      files: { orders: { quantity: "qty", order_date: "date" } },
    });
  });

  it("shows validation problems from the server", async () => {
    postJson.mockRejectedValueOnce(
      problem(422, "invalid_mapping", "The column mapping is incomplete or invalid.", [
        { field: "orders.order_date", message: "required column unmapped" },
      ]),
    );
    render(<MappingReview businessId="b1" datasetId="d1" files={files} />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm mapping and validate" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("orders.order_date: required column unmapped");
    expect(postJson.mock.calls[0]![1]).toEqual({ files: { orders: { quantity: "qty", order_date: null } } });
  });
});
