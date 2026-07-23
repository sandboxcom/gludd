// Companion exports for enforce-tdd.ts.
// opencode's getLegacyPlugins() rejects non-function exports, so named
// constants/functions live here for tests to import.
import * as fs from "node:fs";
import * as path from "node:path";

export const ALLOWLIST_PATTERNS: RegExp[] = [
  /__init__\.py$/,
  /__pycache__\//,
  /\.pyi$/,
  /(^|\/)typing\.py$/,
  /(^|\/)type_defs\.py$/,
  /(^|\/)protocols\.py$/,
  /(^|\/)_types\.py$/,
];
const SRC_PREFIX = "src/general_ludd/";
const TESTS_PREFIX = "tests/";
const DENY_MESSAGE =
  "TDD VIOLATION: write the test FIRST. Create the test file (one of the " +
  "candidates below) BEFORE editing this src/ file. See AGENTS.md " +
  "\"CRITICAL: TDD Policy\". Workflow: (1) write tests/unit/test_<module>.py, " +
  "(2) run it, confirm it fails (red), (3) THEN edit the implementation.";

export function candidateTestPaths(srcFile: string, projectRoot: string): string[] {
  const normalized = srcFile.replace(/\\/g, "/");
  let rel = normalized;
  const srcIdx = rel.indexOf("src/");
  if (srcIdx >= 0) {
    rel = rel.slice(srcIdx + "src/".length);
  }
  if (rel.endsWith(".py")) {
    rel = rel.slice(0, -3);
  }
  const parts = rel.split("/").filter(Boolean);
  if (parts.length === 0) return [];
  const stem = parts.join("_");
  const candidates: string[] = [
    path.join(projectRoot, TESTS_PREFIX, "unit", `test_${stem}.py`),
  ];
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
    return { allow: true };
  }
}
