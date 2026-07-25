// Workflow enforced (see AGENTS.md "CRITICAL: TDD Policy"):
// Layer map (AGENTS.md "Meta-Rule: Guardrail Policy"):
// 3. Agent prompt       — AGENTS.md "CRITICAL: TDD Policy" section
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";
// ── Allowlist ──────────────────────────────────────────────────────────────
// MUST match scripts/check_tdd_compliance.py ALLOWLIST. Type definitions,
// package markers, and protocols don't need behavioral tests.
const ALLOWLIST_PATTERNS: RegExp[] = [
  /__pycache__\//,
  /\.pyi$/,
  /(^|\/)typing\.py$/,
  /(^|\/)type_defs\.py$/,
  /(^|\/)protocols\.py$/,
  /(^|\/)_types\.py$/,
];
// Path-prefix scope: only src/ implementation code is gated.
const SRC_PREFIX = "src/general_ludd/";
const TESTS_PREFIX = "tests/";
const DENY_MESSAGE =
  "TDD VIOLATION: write the test FIRST. Create the test file (one of the " +
  "candidates below) BEFORE editing this src/ file. See AGENTS.md " +
  "\"CRITICAL: TDD Policy\". Workflow: (1) write tests/unit/test_<module>.py, " +
  "(2) run it, confirm it fails (red), (3) THEN edit the implementation.";
// ── Path helpers (mirror check_tdd_compliance.py exactly) ──────────────────
function candidateTestPaths(srcFile: string, projectRoot: string): string[] {
  // Normalize to forward slashes.
  const normalized = srcFile.replace(/\\/g, "/");
  // Strip everything up to and including "src/".
  let rel = normalized;
  const srcIdx = rel.indexOf("src/");
  if (srcIdx >= 0) {
    rel = rel.slice(srcIdx + "src/".length);
  }
  // Drop the .py extension.
  if (rel.endsWith(".py")) {
    rel = rel.slice(0, -3);
  }
  const parts = rel.split("/").filter(Boolean);
  if (parts.length === 0) return [];
  const stem = parts.join("_");
  const candidates: string[] = [
    path.join(projectRoot, TESTS_PREFIX, "unit", `test_${stem}.py`),
  ];
  // Leaf-name candidate (second opinion — matches the python script).
  if (parts.length > 1) {
    const leaf = parts[parts.length - 1];
    candidates.push(
      path.join(projectRoot, TESTS_PREFIX, "unit", `test_${leaf}.py`),
    );
  }
  return candidates;
}
function isAllowlisted(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  return ALLOWLIST_PATTERNS.some(re => re.test(normalized));
}
export function isInitInEmptyDir(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  if (path.basename(normalized) !== "__init__.py") return false;
  const dir = path.dirname(normalized);
  try {
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return false;
    const entries = fs.readdirSync(dir);
    return !entries.some(
      e => e.endsWith(".py") && e !== "__init__.py",
    );
  } catch {
    return false;
  }
}
function isImplementationFile(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false;
  const normalized = filePath.replace(/\\/g, "/");
  if (normalized.includes(TESTS_PREFIX)) return false;
  return normalized.includes(SRC_PREFIX) && normalized.endsWith(".py");
}
interface TddVerdict {
  allow: boolean;
  reason?: string;
  candidates?: string[];
}
export function shouldAllowEdit(
  filePath: string,
  projectRoot: string,
): TddVerdict {
  try {
    if (!isImplementationFile(filePath)) {
      return { allow: true };
    }
    if (isAllowlisted(filePath)) {
      return { allow: true };
    }
    if (isInitInEmptyDir(filePath)) {
      return { allow: true };
    }
    const candidates = candidateTestPaths(filePath, projectRoot);
    const hasTest = candidates.some(c => {
      try {
        return fs.existsSync(c) && fs.statSync(c).isFile();
      } catch {
        return false;
      }
    });
    if (hasTest) {
      return { allow: true };
    }
    return {
      allow: false,
      reason: DENY_MESSAGE,
      candidates,
    };
  } catch {
    // Fail-open: never wedge the editor on a plugin error.
    return { allow: true };
  }
}
// ── DEFAULT IMPLEMENTATION (compiled-in fallback) ──────────────────────────
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard — subagents inherit the
    // orchestrator's enforcement, never their own.
    if (isSubagent()) return;
    reportAlive("enforce-tdd");
    if (process.env.GLUDD_TDD_ENFORCE === "0") return;
    if (input?.tool !== "edit" && input?.tool !== "write") {
      return;
    }
    try {
      const filePath: string =
        output?.args?.filePath ?? output?.args?.path ?? "";
      if (!filePath) {
        return;
      }
      const projectRoot = getProjectRoot();
      const verdict = shouldAllowEdit(filePath, projectRoot);
      if (!verdict.allow) {
        const candidateList = verdict.candidates
          ? `\nExpected test file (one of):\n  - ${verdict.candidates.join("\n  - ")}`
          : "";
        return {
          permissionDecision: "deny",
          message: `${verdict.reason ?? DENY_MESSAGE}${candidateList}`,
        };
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
      return;
    }
  },
};
// ── PROXY PLUGIN (hot-reload aware) ────────────────────────────────────────
export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("enforce-tdd", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
