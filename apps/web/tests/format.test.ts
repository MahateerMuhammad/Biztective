import { describe, expect, it } from "vitest";

import { formatChange, formatCount, formatMetric, formatMoney, formatPercent } from "@/lib/format";

describe("display formatting", () => {
  it("formats money, ratios and counts from exact API strings", () => {
    expect(formatMoney("1234.5678", "USD")).toBe("$1,234.57");
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney("not-a-number", "USD")).toBe("—");
    expect(formatPercent("0.428571")).toBe("42.9%");
    expect(formatPercent(null)).toBe("—");
    expect(formatCount(12345)).toBe("12,345");
    expect(formatMetric("refund_rate", "0.05", "EUR")).toBe("5.0%");
    expect(formatMetric("orders", 3, "EUR")).toBe("3");
    expect(formatMetric("aov", "41.0000", "EUR")).toBe("€41.00");
  });

  it("shows ratio changes in points and amounts as relative change", () => {
    expect(formatChange("refund_rate", { abs: "0.655843", rel: "2.390" }, "USD")).toBe("+65.6 pts");
    expect(formatChange("net_revenue", { abs: "-56.5000", rel: "-0.949580" }, "USD")).toBe("-95.0%");
    expect(formatChange("orders", { abs: 2, rel: null }, "USD")).toBe("2");
    expect(formatChange("gross_profit", { abs: null, rel: null }, "USD")).toBe("—");
    expect(formatChange("gross_profit", undefined, "USD")).toBe("—");
  });
});
