import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // SEC-09: never inject raw HTML.
      "react/no-danger": "error",
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
  {
    // Vendored components (Draggable Widget Grid), used as published: its drag handlers read
    // refs during render by design.
    files: ["src/components/ui/draggable-widget-grid.tsx"],
    rules: { "react-hooks/refs": "off", "react-hooks/immutability": "off", "@typescript-eslint/ban-ts-comment": "off" },
  },
  {
    // Vendored Sun/Moon toggle: it reads the stored theme once, in an effect, because that is browser-only.
    files: ["src/components/ui/sun-moon-theme-toggle.tsx"],
    rules: { "react-hooks/set-state-in-effect": "off" },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
    "src/lib/api/schema.d.ts",
  ]),
]);
