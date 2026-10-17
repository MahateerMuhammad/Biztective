import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KindBadge } from "@/components/kind-badge";
import { Progress } from "@/components/investigation/progress";
import { ProofChain } from "@/components/investigation/proof-chain";
import { Pager, cursorParam } from "@/components/pager";
import { StrategyComparison } from "@/components/recovery/strategy-comparison";
import { Segments } from "@/components/segments";
import type { InvestigationIncident, PlanContent } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ usePathname: () => "/businesses/b1/investigations", useRouter: () => ({ refresh: vi.fn() }) }));
// Recharts measures the DOM; the chart is a visual aid with a table alternative, tested via the table.
vi.mock("@/components/charts/strategy-chart", () => ({ StrategyChart: () => <div data-testid="chart" /> }));

afterEach(cleanup);

const incident: InvestigationIncident = {
  id: "i1",
  rank: 1,
  key: "discount_rate|product|P1",
  metric: "discount_rate",
  metric_label: "Discount rate",
  dimension: "product",
  value: "P1",
  severity: "high",
  investigated: true,
  observations: [[{ text: "Discount rate was " }, { value: "35.0%", evidence_id: "e1", field: "current_value" }]],
  evidence: [
    { id: "e1", kind: "incident_detection", requested_by: "detection" },
    { id: "e2", kind: "metric_breakdown", requested_by: "llm" },
  ],
  root_cause_hypothesis_id: "h1",
  explained_by_context: [],
  recommendations: [],
  human_context: [],
  data_gaps: [],
  hypotheses: [
    {
      id: "h1",
      template: "H_PRICE_DISCOUNT",
      claim: "",
      source: "catalog",
      status: "supported",
      confidence: "0.9",
      data_confidence: "0.9",
      human_confidence: null,
      interpretation: null,
      conflicts: [],
      criteria: [
        {
          key: "c1",
          description: "The discount rate rose by at least 2 pp.",
          polarity: "for",
          source: "data",
          satisfied: true,
          basis: [[{ text: "rose by " }, { value: "33.5 pp", evidence_id: "e1", field: "delta_abs" }]],
        },
        { key: "c2", description: "Someone confirms it was unplanned.", polarity: "for", source: "human", satisfied: null, basis: [] },
      ],
    },
    {
      id: "h2",
      template: "H_DATA_QUALITY",
      claim: "",
      source: "catalog",
      status: "refuted",
      confidence: "0",
      data_confidence: null,
      human_confidence: null,
      interpretation: null,
      conflicts: [],
      criteria: [],
    },
  ],
};

describe("ProofChain", () => {
  it("separates facts, hypotheses, evidence and the conclusion, with source links", () => {
    render(<ProofChain incident={incident} evidenceBase="/businesses/b1/evidence" />);
    const steps = screen.getByRole("list", { name: "Proof chain" }).children;
    expect([...steps].map((s) => s.querySelector("h4")?.textContent)).toEqual([
      "1. What changed Fact",
      "2. Competing explanations Hypothesis",
      "3. Evidence trail Fact",
      "4. Conclusion",
    ]);
    // Every cited value links to the evidence it came from.
    expect(screen.getByRole("link", { name: "35.0%" }).getAttribute("href")).toBe("/businesses/b1/evidence/e1");
    expect(screen.getByRole("link", { name: "33.5 pp" }).getAttribute("href")).toBe("/businesses/b1/evidence/e1");
    // Who asked for each piece of evidence is visible.
    expect(screen.getByRole("link", { name: /Breakdown.*chosen by AI/ })).toBeTruthy();
    expect(screen.getByText("Criterion met:", { exact: false })).toBeTruthy();
    expect(screen.getByText("Criterion not determined:", { exact: false })).toBeTruthy();
    expect(screen.getByText(/Root cause:/).parentElement?.textContent).toContain("90% confidence");
  });
});

describe("Segments", () => {
  it("renders untrusted text as text, never as markup", () => {
    const { container } = render(<Segments segments={[{ text: "<img src=x onerror=alert(1)>" }]} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)>");
  });
});

describe("Progress", () => {
  it("lists agent activity newest first in plain words", () => {
    render(
      <Progress
        working
        events={[
          { seq: 1, type: "run.started", actor_kind: "system", occurred_at: "2026-10-17T10:00:00Z", payload: {} },
          { seq: 2, type: "clarification.requested", actor_kind: "system", occurred_at: "2026-10-17T10:00:05Z", payload: {} },
          { seq: 3, type: "something.new", actor_kind: "human", occurred_at: "2026-10-17T10:00:09Z", payload: {} },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("something new");
    expect(items[0]).toContain("by a person");
    expect(items[1]).toContain("Asked you a question");
    expect(items[2]).toContain("Investigation started");
  });
});

describe("KindBadge", () => {
  it("names the kind in words, not by colour alone", () => {
    render(
      <>
        <KindBadge kind="fact" />
        <KindBadge kind="hypothesis" />
        <KindBadge kind="estimate" />
        <KindBadge kind="human" />
        <KindBadge kind="action" />
      </>,
    );
    for (const word of ["Fact", "Hypothesis", "Estimate", "Your input", "Action"]) expect(screen.getByText(word)).toBeTruthy();
  });
});

describe("Pager", () => {
  it("accepts only API-shaped cursors and links to the next page", () => {
    expect(cursorParam("MDFhMTQ3ODgtNDAzNg")).toBe("MDFhMTQ3ODgtNDAzNg");
    expect(cursorParam("javascript:alert(1)")).toBeUndefined();
    expect(cursorParam(["a", "b"])).toBeUndefined();
    render(<Pager path="/x" cursor="abc" next="def" />);
    expect(screen.getByRole("link", { name: "← First page" }).getAttribute("href")).toBe("/x");
    expect(screen.getByRole("link", { name: "Next page →" }).getAttribute("href")).toBe("/x?cursor=def");
  });

  it("renders nothing for a single page", () => {
    const { container } = render(<Pager path="/x" next={null} />);
    expect(container.textContent).toBe("");
  });
});

describe("StrategyComparison", () => {
  it("compares estimates side by side and marks risky actions and missing inputs", () => {
    const scenario = (benefit: string, cost: string, net: string) => ({ benefit, cost, net });
    const content = {
      currency: "USD",
      selected: ["s1"],
      strategies: [
        {
          id: "s1",
          title: "Review discount rules",
          incident: { id: "i1", key: "discount_rate|product|P1", rank: 1 },
          action_types: ["review_task"],
          risky_action_types: [],
          tradeoffs: ["Slower"],
          estimate_status: "computed",
          estimate: { scenarios: { low: scenario("100", "20", "80"), mid: scenario("200", "30", "170"), high: scenario("300", "40", "260") } },
        },
        {
          id: "s2",
          title: "Cap discounts",
          incident: { id: "i1", key: "discount_rate|product|P1", rank: 1 },
          action_types: ["review_task", "discount_policy"],
          risky_action_types: ["discount_policy"],
          tradeoffs: [],
          estimate_status: "missing_assumptions",
          estimate: null,
        },
      ],
    } as unknown as PlanContent;
    render(<StrategyComparison content={content} />);
    const table = screen.getByRole("table", { name: "Competing strategies compared" });
    expect(table.textContent).toContain("$170.00");
    expect(table.textContent).toContain("$100.00–$300.00");
    expect(table.textContent).toContain("Needs your cost inputs");
    expect(table.textContent).toContain("discount policy (risky)");
    expect(screen.getByText("Selected")).toBeTruthy();
    expect(screen.getByTestId("chart")).toBeTruthy();
  });
});

describe("NavLink", () => {
  it("marks the current section for assistive technology", async () => {
    const { NavLink } = await import("@/components/nav-link");
    render(
      <>
        <NavLink href="/businesses/b1" exact>
          Business health
        </NavLink>
        <NavLink href="/businesses/b1/investigations">Investigations</NavLink>
      </>,
    );
    expect(screen.getByRole("link", { name: "Investigations" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Business health" }).getAttribute("aria-current")).toBeNull();
    fireEvent.focus(screen.getByRole("link", { name: "Investigations" }));
  });
});
