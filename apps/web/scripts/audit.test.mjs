import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./audit.mjs", import.meta.url));

function gate(report, exitCode = 1) {
  const directory = mkdtempSync(join(tmpdir(), "root-audit-test-"));
  try {
    const npm = join(directory, "npm");
    writeFileSync(npm, `#!${process.execPath}\nprocess.stdout.write(process.env.ROOT_AUDIT_REPORT);process.exit(Number(process.env.ROOT_AUDIT_EXIT));\n`);
    chmodSync(npm, 0o700);
    return spawnSync(process.execPath, [script], {
      encoding: "utf8",
      env: { ...process.env, PATH: `${directory}:${dirname(process.execPath)}:${process.env.PATH ?? ""}`,
        ROOT_AUDIT_REPORT: typeof report === "string" ? report : JSON.stringify(report),
        ROOT_AUDIT_EXIT: String(exitCode) },
    });
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

const advisory = (id, severity = "high") => ({
  vulnerabilities: { dependency: { via: [{ name: "dependency", title: "test advisory", severity,
    url: `https://github.com/advisories/${id}` }] } },
});

test("a successful clean audit passes", () => {
  assert.equal(gate({ vulnerabilities: {} }, 0).status, 0);
});

test("registry failure cannot be mistaken for a clean audit", () => {
  const result = gate({ error: { code: "ENOTFOUND", summary: "registry unavailable" } });
  assert.equal(result.status, 1);
  assert.doesNotMatch(result.stdout, /gate passed/);
});

for (const [label, report] of [
  ["invalid JSON", "not JSON"], ["missing results", {}], ["null results", null],
  ["array results", { vulnerabilities: [] }],
  ["error beside results", { vulnerabilities: {}, error: { code: "EAUDIT" } }],
  ["empty error beside results", { vulnerabilities: {}, error: null }],
  ["unknown advisory severity", advisory("GHSA-unknown", "unknown")],
  ["malformed entry", { vulnerabilities: { dependency: {} } }],
  ["malformed advisory", { vulnerabilities: { dependency: { via: [null] } } }],
]) {
  test(`${label} fails closed`, () => assert.equal(gate(report).status, 1));
}

for (const severity of ["high", "critical"]) {
  test(`an unexcepted ${severity} advisory fails`, () => {
    assert.equal(gate(advisory("GHSA-unapproved", severity)).status, 1);
  });
}

test("the existing narrow AMD-7 exception remains allowed before expiry", () => {
  const result = gate(advisory("GHSA-vfj7-8cjw-p6xm"));
  assert.equal(result.status, new Date().toISOString().slice(0, 10) <= "2027-01-31" ? 0 : 1);
});

test("a lower severity advisory passes", () => {
  assert.equal(gate(advisory("GHSA-unapproved", "moderate")).status, 0);
});
