// npm audit gate (SEC-21): fail on any high/critical advisory except documented, dated exceptions.
// Exceptions must be recorded as master-plan amendments with an expiry (see AMD-7).
import { execFileSync } from "node:child_process";

const EXCEPTIONS = {
  // braces: no patched release exists; reachable only via eslint-config-next (dev-only lint
  // tooling over repository-controlled glob patterns). Not present in production dependencies.
  "GHSA-vfj7-8cjw-p6xm": { reason: "AMD-7", expires: "2027-01-31" },
};

let output;
try {
  output = execFileSync("npm", ["audit", "--json"], { encoding: "utf8" });
} catch (error) {
  output = error.stdout; // npm audit exits non-zero when it finds anything
}
const report = JSON.parse(output);
const today = new Date().toISOString().slice(0, 10);
const failures = new Set();

for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vulnerability.via) {
    if (typeof via !== "object" || !["high", "critical"].includes(via.severity)) continue;
    const id = via.url?.split("/").pop() ?? "unknown";
    const exception = EXCEPTIONS[id];
    if (!exception || exception.expires < today) {
      failures.add(`${via.severity}: ${via.name} ${id} ${via.title}`);
    }
  }
}

if (failures.size > 0) {
  console.error("Unexcepted high/critical advisories:\n" + [...failures].join("\n"));
  process.exit(1);
}
console.log("npm audit gate passed (only documented exceptions present).");
