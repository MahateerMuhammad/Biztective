import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StartInvestigation } from "@/app/(app)/businesses/[businessId]/datasets/[datasetId]/analytics/start-investigation";
import { QuestionForm } from "@/components/investigation/question-form";
import { VersionDiff } from "@/components/investigation/version-diff";
import { Segments } from "@/components/segments";
import { ApiProblem, type Clarification, type VersionComparison } from "@/lib/api/types";
import { templateLabel } from "@/lib/format";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));

const postJson = vi.fn();
vi.mock("@/lib/api/client", () => ({ postJson: (...args: unknown[]) => postJson(...args) }));

function problem(status: number, code: string, detail: string) {
  return new ApiProblem({ type: "t", title: "t", status, detail, code, request_id: null });
}

const question: Clarification = {
  id: "c1",
  incident_id: "i1",
  incident_key: "discount_rate|product|P1",
  criterion: "H_PRICE_DISCOUNT.C3",
  trigger: "CT-5",
  status: "awaiting",
  question: "Was the higher discounting in the period planned?",
  claim: "Heavier discounting is driving the change.",
  why: "The data cannot show intent.",
  options: [
    { id: "planned_campaign", label: "Yes, a planned clearance" },
    { id: "unplanned", label: "No, not planned" },
    { id: "unsure", label: "Not sure" },
  ],
  response_schema: {},
  checkpoint_id: "cp-1",
  asked_at: null,
  answered_at: null,
  applied_at: null,
  answer: null,
};
const answerPath = "/api/v1/businesses/b1/investigations/inv1/clarifications/c1/answer";

beforeEach(() => {
  push.mockReset();
  refresh.mockReset();
  postJson.mockReset();
});
afterEach(cleanup);

describe("QuestionForm", () => {
  it("needs an option or text before it can be sent", () => {
    render(<QuestionForm businessId="b1" investigationId="inv1" clarification={question} />);
    const send = screen.getByRole("button", { name: "Send answer" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Anything else ROOT should know/), { target: { value: "   " } });
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Not sure"));
    expect(send.disabled).toBe(false);
    expect(screen.getByText(/never treats them as instructions or as approval/)).toBeDefined();
  });

  it("sends the chosen option, the text and the paused checkpoint, then refreshes", async () => {
    postJson.mockResolvedValueOnce({ status: "answered" });
    render(<QuestionForm businessId="b1" investigationId="inv1" clarification={question} />);
    fireEvent.click(screen.getByLabelText("Yes, a planned clearance"));
    fireEvent.change(screen.getByLabelText(/Anything else ROOT should know/), {
      target: { value: "Spring clearance of old stock." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const [path, body, key] = postJson.mock.calls[0]!;
    expect(path).toBe(answerPath);
    expect(body).toEqual({
      checkpoint_id: "cp-1",
      option_id: "planned_campaign",
      free_text: "Spring clearance of old stock.",
    });
    expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it("keeps the key for a retry and changes it when the answer changes", async () => {
    postJson.mockRejectedValueOnce(new TypeError("network")).mockRejectedValueOnce(new TypeError("network"));
    render(<QuestionForm businessId="b1" investigationId="inv1" clarification={question} />);
    fireEvent.click(screen.getByLabelText("No, not planned"));
    const send = screen.getByRole("button", { name: "Send answer" });
    fireEvent.click(send);
    await screen.findByText("Your answer was not saved");
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => expect(postJson).toHaveBeenCalledTimes(2));
    expect(postJson.mock.calls[1]![2]).toBe(postJson.mock.calls[0]![2]);
    postJson.mockResolvedValueOnce({});
    fireEvent.click(screen.getByLabelText("Not sure"));
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => expect(postJson).toHaveBeenCalledTimes(3));
    expect(postJson.mock.calls[2]![2]).not.toBe(postJson.mock.calls[0]![2]);
    expect(postJson.mock.calls[2]![1]).toEqual({ checkpoint_id: "cp-1", option_id: "unsure" });
  });

  it.each(["stale_checkpoint", "already_answered"])("reloads when the question changed (%s)", async (code) => {
    postJson.mockRejectedValueOnce(problem(409, code, "The investigation has moved on."));
    render(<QuestionForm businessId="b1" investigationId="inv1" clarification={question} />);
    fireEvent.click(screen.getByLabelText("Not sure"));
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect((await screen.findByRole("alert")).textContent).toContain("This question has changed");
    expect(refresh).toHaveBeenCalled();
  });

  it("shows a validation problem without reloading", async () => {
    postJson.mockRejectedValueOnce(problem(422, "validation_error", "Choose one of the offered options."));
    render(<QuestionForm businessId="b1" investigationId="inv1" clarification={question} />);
    fireEvent.change(screen.getByLabelText(/Anything else ROOT should know/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Choose one of the offered options.");
    expect(refresh).not.toHaveBeenCalled();
    expect(postJson.mock.calls[0]![1]).toEqual({ checkpoint_id: "cp-1", free_text: "x" });
  });
});

describe("Segments", () => {
  it("renders values and entities as plain text, never as markup", () => {
    const { container } = render(
      <p>
        <Segments
          segments={[
            { text: "<img src=x onerror=alert(1)> rose to " },
            { value: "20.2%", evidence_id: "e1", field: "current_value" },
            { text: " on " },
            { entity: "Mat <b>(P1)</b>", kind: "product" },
          ]}
        />
      </p>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> rose to 20.2% on Mat <b>(P1)</b>");
    expect(screen.getByTitle("From evidence e1 (current_value)").textContent).toBe("20.2%");
  });
});

const comparison: VersionComparison = {
  from_version: 1,
  to_version: 2,
  caused_by: {
    human_input_id: "h1",
    criterion: "H_PRICE_DISCOUNT.C3",
    option_id: "planned_campaign",
    effect: "intended",
    answered_by: "u1",
    answered_at: "2026-10-17T10:00:00",
  },
  measurements_preserved: true,
  incidents: [
    {
      key: "discount_rate|product|P1",
      measurements_preserved: true,
      root_cause: { from: "H_PRICE_DISCOUNT", to: null },
      explained_by_context: { added: ["H_PRICE_DISCOUNT"], removed: [] },
      hypotheses: [
        { template: "H_COST_PRICE", added: true, now: { status: "refuted" } },
        {
          template: "H_PRICE_DISCOUNT",
          changes: {
            interpretation: { from: null, to: "intended" },
            human_confidence: { from: null, to: "1.0000" },
          },
        },
      ],
      recommendations: { added: ["Track the planned campaign."], removed: ["Review discount rules."] },
    },
    { key: "discount_rate|category|C1", measurements_preserved: true, hypotheses: [] },
  ],
  findings: {
    summary: { from: "One unplanned cause.", to: "Explained by a deliberate decision." },
    root_causes: { added: [], removed: ["Discounting."] },
    explained_by_context: { added: ["Discounting."], removed: [] },
    conflicts: { added: [], removed: [] },
    caveats: { added: [], removed: [] },
  },
};

describe("VersionDiff", () => {
  it("shows what the answer changed and that the measurements are intact", () => {
    render(<VersionDiff comparison={comparison} />);
    expect(screen.getByRole("status").textContent).toContain("All original measurements are unchanged");
    expect(screen.getByText(/said it was a deliberate decision \(planned_campaign\)/)).toBeDefined();
    expect(screen.getByText(/Explained by a deliberate decision\./)).toBeDefined();
    const items = screen.getByRole("list", { name: "Changes per incident" });
    expect(items.textContent).toContain("Root cause changed: Heavier discounting → None");
    expect(items.textContent).toContain("New alternative investigated: Prices relative to cost (Ruled out)");
    expect(items.textContent).toContain("Interpretation none → intended");
    expect(items.textContent).toContain("Your context none → 100%");
    expect(items.textContent).toContain("No longer recommended: Review discount rules.");
    expect(items.textContent).toContain("Now recommended: Track the planned campaign.");
    expect(items.textContent).not.toContain("discount_rate|category|C1"); // unchanged incident
  });

  it("warns when a measurement no longer matches", () => {
    render(
      <VersionDiff
        comparison={{
          ...comparison,
          caused_by: null,
          measurements_preserved: false,
          incidents: [{ key: "k", measurements_preserved: false, hypotheses: [] }],
          findings: { ...comparison.findings, summary: null },
        }}
      />,
    );
    expect(screen.getAllByRole("alert")[0]!.textContent).toContain("Measurements differ");
    expect(screen.getByText("The summary did not change.")).toBeDefined();
  });

  it("says so when nothing changed", () => {
    render(
      <VersionDiff
        comparison={{ ...comparison, incidents: [{ key: "k", measurements_preserved: true, hypotheses: [] }] }}
      />,
    );
    expect(screen.getByText("No hypothesis, root cause or recommendation changed.")).toBeDefined();
  });
});

describe("StartInvestigation", () => {
  it("starts once and opens the workspace", async () => {
    postJson.mockResolvedValueOnce({ id: "inv9" });
    render(<StartInvestigation businessId="b1" datasetId="d1" />);
    fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/businesses/b1/investigations/inv9"));
    const [path, body, key] = postJson.mock.calls[0]!;
    expect(path).toBe("/api/v1/businesses/b1/investigations");
    expect(body).toEqual({ dataset_id: "d1" });
    expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it("shows why a start was refused", async () => {
    postJson.mockRejectedValueOnce(problem(409, "analysis_not_ready", "The dataset's analysis must finish first."));
    render(<StartInvestigation businessId="b1" datasetId="d1" />);
    fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
    expect((await screen.findByRole("alert")).textContent).toContain("analysis must finish");
  });
});

describe("templateLabel", () => {
  it("names catalog templates and falls back safely", () => {
    expect(templateLabel("H_COST_PRICE")).toBe("Prices relative to cost");
    expect(templateLabel(null)).toBe("None");
    expect(templateLabel("H_UNKNOWN")).toBe("H_UNKNOWN");
  });
});
