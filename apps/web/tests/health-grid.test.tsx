import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { DEFAULT_WIDGETS, restoreLayout } from "@/components/health/health-grid";

describe("restoreLayout", () => {
  const ids = (items: { id: string }[]) => items.map((i) => i.id);

  it("falls back to the default order without a saved layout or with a broken one", () => {
    expect(restoreLayout(DEFAULT_WIDGETS, null)).toBe(DEFAULT_WIDGETS);
    expect(restoreLayout(DEFAULT_WIDGETS, "not json")).toBe(DEFAULT_WIDGETS);
    expect(restoreLayout(DEFAULT_WIDGETS, '{"a":1}')).toBe(DEFAULT_WIDGETS);
    expect(restoreLayout(DEFAULT_WIDGETS, "[1,2]")).toBe(DEFAULT_WIDGETS);
  });

  it("applies a saved order, drops ids that no longer exist and appends new widgets", () => {
    const saved = JSON.stringify(["decisions", "gone", "orders", "decisions"]);
    const restored = ids(restoreLayout(DEFAULT_WIDGETS, saved));
    expect(restored.slice(0, 2)).toEqual(["decisions", "orders"]);
    expect(restored).toHaveLength(DEFAULT_WIDGETS.length);
    expect(new Set(restored).size).toBe(DEFAULT_WIDGETS.length);
  });
});

/**
 * The grid component is used exactly as published, so its tiler is exercised through its source:
 * the layout functions are transpiled out of the file and run for the ROOT widget sizes.
 */
function loadTiler() {
  const src = readFileSync(resolve(process.cwd(), "src/components/ui/draggable-widget-grid.tsx"), "utf8");
  const spans = src.slice(src.indexOf("const SPANS"), src.indexOf("const SIZE_LABELS"));
  const layout = src.slice(src.indexOf("interface Placement"), src.indexOf("/** Reorders `items`"));
  const js = ts.transpileModule(`${spans}\n${layout}\nreturn { tile };`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 },
  }).outputText;
  return new Function(js)() as { tile: (items: { id: string; size: string }[], columns: number) => unknown[] | null };
}

describe("widget layout", () => {
  const { tile } = loadTiler();

  it("tiles the default order exactly (no pack() fallback) at 2, 3 and 4 columns", () => {
    for (const columns of [2, 3, 4]) expect(tile(DEFAULT_WIDGETS, columns)).not.toBeNull();
  });

  it("tiles any order the user can drag to", () => {
    let seed = 7;
    const random = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
    for (let n = 0; n < 200; n++) {
      const order = [...DEFAULT_WIDGETS].sort(() => random() - 0.5);
      for (const columns of [2, 3, 4]) expect(tile(order, columns)).not.toBeNull();
    }
  });
});
