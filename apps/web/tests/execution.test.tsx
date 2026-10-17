import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectSheet } from "@/components/integrations/connect-sheet";
import { ExecutionHistory } from "@/components/recovery/execution-history";
import { ExecutionPanel } from "@/components/recovery/execution-panel";
import { ApiProblem, type ActionDetail } from "@/lib/api/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh }) }));

const postJson = vi.fn();
vi.mock("@/lib/api/client", () => ({ postJson: (...args: unknown[]) => postJson(...args) }));

const approved = { version: 2, content_hash: "a".repeat(64) };

function action(overrides: Partial<ActionDetail> = {}): ActionDetail {
  return {
    id: "act-1",
    plan_id: "plan-1",
    plan_version: 2,
    content_hash: "a".repeat(64),
    approval_id: "dec-1",
    kind: "sheets.write_recovery_tasks",
    status: "verified",
    error_code: null,
    attempt_count: 1,
    payload_hash: "b".repeat(64),
    destination: { spreadsheet_id: "sheet", tabs: ["ROOT Review Tasks"], url: "https://docs.google.com/spreadsheets/d/sheet" },
    rows: { "ROOT Review Tasks": 1 },
    not_executed: [{ strategy: "S_GOODWILL_OUTREACH", action_type: "customer_offer", reason: "not_supported" }],
    external_ref: { spreadsheet_id: "sheet", tabs: ["ROOT Review Tasks"], updated_ranges: { "ROOT Review Tasks": "'ROOT Review Tasks'!A2:L2" } },
    verification: {
      ok: true,
      tabs: { "ROOT Review Tasks": { expected: 1, found: 1 } },
      missing: [],
      duplicated: [],
      mismatched: [],
      unexpected: [],
      checked_at: "2026-10-17T00:00:00Z",
    },
    requested_by: "u1",
    created_at: "2026-10-17T00:00:00Z",
    updated_at: "2026-10-17T00:00:00Z",
    finished_at: "2026-10-17T00:00:01Z",
    payload: { tabs: [] },
    attempts: [
      {
        attempt: 1,
        status: "completed",
        error_code: null,
        writes_sent: true,
        calls: [
          { op: "metadata", status: 200, ms: 5 },
          { op: "append", status: 200, ms: 9 },
        ],
        started_at: "2026-10-17T00:00:00Z",
        finished_at: "2026-10-17T00:00:01Z",
      },
    ],
    audit: [{ action: "action.verified", actor_user_id: null, at: "2026-10-17T00:00:01Z", metadata: {} }],
    ...overrides,
  };
}

beforeEach(() => {
  postJson.mockReset();
  refresh.mockReset();
});
afterEach(cleanup);

describe("ExecutionPanel", () => {
  it("starts execution with the approved version, its hash and one stable idempotency key", async () => {
    postJson.mockResolvedValue({ id: "act-1", status: "pending", created: true });
    render(<ExecutionPanel businessId="b1" planId="p1" approved={approved} integrationReady current={null} role="owner" />);
    const button = screen.getByRole("button", { name: "Execute in Google Sheets" });
    fireEvent.click(button);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    fireEvent.click(button);
    await waitFor(() => expect(postJson).toHaveBeenCalledTimes(2));
    const [path, body, key] = postJson.mock.calls[0]!;
    expect(path).toBe("/api/v1/businesses/b1/recovery-plans/p1/actions");
    expect(body).toEqual({ version: 2, content_hash: "a".repeat(64) });
    expect(postJson.mock.calls[1]![2]).toBe(key); // a repeated click replays the same request
    expect(screen.getByText(/No customer is contacted/)).toBeTruthy();
  });

  it("is hidden from non-owners and asks for a spreadsheet when none is connected", () => {
    const { container } = render(
      <ExecutionPanel businessId="b1" planId="p1" approved={approved} integrationReady current={null} role="analyst" />,
    );
    expect(container.textContent).toBe("");
    cleanup();
    render(<ExecutionPanel businessId="b1" planId="p1" approved={approved} integrationReady={false} current={null} role="owner" />);
    expect(screen.getByRole("link", { name: "Connect a spreadsheet" }).getAttribute("href")).toBe("/businesses/b1/integrations");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers a retry for actions that need review and shows refusals", async () => {
    postJson.mockRejectedValue(
      new ApiProblem({ type: "t", title: "t", status: 409, detail: "This action can no longer be executed.", code: "x", request_id: null }),
    );
    render(
      <ExecutionPanel
        businessId="b1"
        planId="p1"
        approved={approved}
        integrationReady
        current={action({ status: "manual_review", error_code: "verification_mismatch" })}
        role="owner"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(screen.getByText("This action can no longer be executed.")).toBeTruthy());
    expect(postJson.mock.calls[0]![0]).toBe("/api/v1/businesses/b1/actions/act-1/retry");
  });
});

describe("ExecutionHistory", () => {
  it("shows the verified result, the read-back counts, attempts, calls and audit trail", () => {
    render(<ExecutionHistory actions={[action()]} />);
    expect(screen.getByText(/Done and verified/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the spreadsheet" }).getAttribute("href")).toBe(
      "https://docs.google.com/spreadsheets/d/sheet",
    );
    expect(screen.getByText("'ROOT Review Tasks'!A2:L2")).toBeTruthy();
    expect(screen.getByText("metadata:200 append:200")).toBeTruthy();
    expect(screen.getByText(/customer offer/)).toBeTruthy();
    expect(screen.getByText(/action.verified · by the executor/)).toBeTruthy();
  });

  it("explains review states and lists the rows that did not match", () => {
    render(
      <ExecutionHistory
        actions={[
          action({
            status: "manual_review",
            error_code: "verification_mismatch",
            verification: { ...action().verification!, ok: false, mismatched: ["k-0001"] },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Needs review/)).toBeTruthy();
    expect(screen.getByText(/Biztective did not overwrite them/)).toBeTruthy();
    expect(screen.getByText("Rows changed: k-0001")).toBeTruthy();
  });
});

describe("ConnectSheet", () => {
  it("binds, shows the one-time code and verifies", async () => {
    postJson.mockResolvedValueOnce({
      id: "i1",
      kind: "google_sheets",
      status: "pending_verification",
      spreadsheet_id: "sheet",
      spreadsheet_url: "u",
      service_account_email: "root@p.iam.gserviceaccount.com",
      verify_expires_at: "2026-10-17T01:00:00Z",
      verified_at: null,
      created_at: "2026-10-17T00:00:00Z",
      verification_code: "ROOT-ABCDEFGHIJ",
      verify_cell: "'ROOT_VERIFY'!A1",
    });
    render(<ConnectSheet businessId="b1" pending={null} />);
    fireEvent.change(screen.getByLabelText(/Google Sheets link/), {
      target: { value: "https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("ROOT-ABCDEFGHIJ")).toBeTruthy();
    expect(screen.getByText("root@p.iam.gserviceaccount.com")).toBeTruthy();
    postJson.mockResolvedValueOnce({});
    fireEvent.click(screen.getByRole("button", { name: "Check and verify" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(postJson.mock.calls[1]![0]).toBe("/api/v1/businesses/b1/integrations/i1/verify");
  });
});
