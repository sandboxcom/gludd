/**
 * enforce-tdd.ts — deny edits/writes to src/ implementation files when no
 * corresponding test file exists yet. Mechanically enforces test-first (TDD).
 *
 * PROBLEM: scripts/check_tdd_compliance.py only runs at COMMIT time. By then
 * an agent has already written implementation code with no test — the damage
 * is done, tokens are spent, and the agent must either backtrack or bypass.
 * deepseek repeatedly skipped writing tests first, leading to broken commits.
 *
 * FIX: block the editor itself. You cannot write to src/general_ludd/foo.py
 * until tests/unit/test_general_ludd_foo.py (or tests/unit/test_foo.py)
 * already exists on disk. The agent is forced to create the test file first.
 *
 * Workflow enforced (see AGENTS.md "CRITICAL: TDD Policy"):
 *   1. Write tests/unit/test_<module>.py       — ALLOWED (it's a test file)
 *   2. Run it, confirm RED                     — (TDD red phase)
 *   3. Write/edit src/general_ludd/<module>.py — ALLOWED (test now exists)
 *   4. Run it, confirm GREEN                   — (TDD green phase)
 *
 * Skip step 1 → step 3 is DENIED.
 *
 * Candidate-path logic mirrors scripts/check_tdd_compliance.py
 * _candidate_test_paths() EXACTLY so the editor and the commit-time gate
 * agree on where the test must live. Divergence = conflicting verdicts.
 *
 * Layer map (AGENTS.md "Meta-Rule: Guardrail Policy"):
 *   1. Config permission  — n/a (content/path-based, not a tool ban)
 *   2. Runtime hook       — THIS FILE (tool.execute.before on edit/write)
 *   3. Agent prompt       — AGENTS.md "CRITICAL: TDD Policy" section
 *   + Behavior pin         — tests/unit/test_enforce_tdd_plugin.py
 *   + Runtime pin          — .opencode/plugin/enforce-tdd.test.node.mjs
 *   + Commit-time pin      — scripts/check_tdd_compliance.py (backstop)
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook
 * functions check /tmp/gludd-hot-enforce-tdd.js on every invocation.  If
 * present and newer than cached, the hot module's hook overrides the
 * compiled-in default.  Run `make hot-reload-plugins` after editing.
 */
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";

// ── Allowlist ──────────────────────────────────────────────────────────────
// MUST match scripts/check_tdd_compliance.py ALLOWLIST. Type definitions,
// package markers, and protocols don't need behavioral tests.
const ALLOWLIST_PATTERNS: RegExp[] = [
  /__init__\.py$/,
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

/**
 * Compute the candidate test file paths for a given src/ file.
 *
 * Mirrors scripts/check_tdd_compliance.py::_candidate_test_paths so the
 * real-time plugin and the commit-time gate agree.
 *
 * For src/general_ludd/daemon.py:
 *   → tests/unit/test_general_ludd_daemon.py
 *   → tests/unit/test_daemon.py
 *
 * For src/general_ludd/foo/bar.py:
 *   → tests/unit/test_general_ludd_foo_bar.py
 *   → tests/unit/test_bar.py
 */
export function candidateTestPaths(srcFile: string, projectRoot: string): string[] {
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

export function isAllowlisted(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  return ALLOWLIST_PATTERNS.some(re => re.test(normalized));
}

/** Is this path under src/general_ludd/ (in-scope for the TDD gate)? */
export function isImplementationFile(filePath: string): boolean {
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

/**
 * Core decision: should this edit/write be allowed under the TDD rule?
 *
 * Returns {allow: true} for:
 *   - non-src/ paths (docs, configs, tests themselves)
 *   - allowlisted files (__init__.py, *.pyi, protocols.py, etc.)
 *   - src/ files that already have a corresponding test file on disk
 *
 * Returns {allow: false, candidates} for src/ files with NO test file.
 */
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
