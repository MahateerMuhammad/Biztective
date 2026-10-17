// npm audit gate (SEC-21): fail on any high/critical advisory except documented, dated exceptions.
// Exceptions must be recorded as master-plan amendments with an expiry (see AMD-7).
import { execFileSync } from "node:child_process";

const EXCEPTIONS = {
  // braces: no patched release exists; reachable only via eslint-config-next (dev-only lint
  // tooling over repository-controlled glob patterns). Not present in production dependencies.
  "GHSA-vfj7-8cjw-p6xm": { reason: "AMD-7", expires: "2027-01-31" },
};

function run() {
  let output;
  try {
    output = execFileSync("npm", ["audit", "--json"], { encoding: "utf8" });
  } catch (error) {
    // npm exits nonzero for findings, but also for registry and command failures.
    output = error.stdout;
  }
  const report = JSON.parse(output);
  if (!report || typeof report !== "object" || Object.hasOwn(report, "error") ||
      !report.vulnerabilities || typeof report.vulnerabilities !== "object" ||
      Array.isArray(report.vulnerabilities)) {
    throw new Error("npm audit did not return a complete vulnerability report");
  }
  const today = new Date().toISOString().slice(0, 10);
  const failures = new Set();

  for (const vulnerability of Object.values(report.vulnerabilities)) {
    if (!vulnerability || typeof vulnerability !== "object" || !Array.isArray(vulnerability.via)) {
      throw new Error("npm audit returned a malformed vulnerability entry");
    }
    for (const via of vulnerability.via) {
      if (typeof via === "string") continue; // npm's reference to another affected package
      if (!via || typeof via !== "object" || typeof via.severity !== "string" ||
          typeof via.url !== "string" ||
          !["info", "low", "moderate", "high", "critical"].includes(via.severity)) {
        throw new Error("npm audit returned a malformed advisory");
      }
      if (!["high", "critical"].includes(via.severity)) continue;
      const id = via.url.split("/").pop() ?? "unknown";
      const exception = EXCEPTIONS[id];
      if (!exception || exception.expires < today) {
        failures.add(`${via.severity}: ${via.name} ${id} ${via.title}`);
      }
    }
  }

  if (failures.size > 0) {
    throw new Error("Unexcepted high/critical advisories:\n" + [...failures].join("\n"));
  }
  console.log("npm audit gate passed (only documented exceptions present).");
}

try {
  run();
} catch (error) {
  console.error(error instanceof Error ? error.message : "npm audit gate failed");
  process.exitCode = 1;
}
