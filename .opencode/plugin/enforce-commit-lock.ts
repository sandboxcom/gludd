// Per AGENTS.md commit-serialization guardrail (Wave 12-14 incidents): multiple
// Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
import * as fs from "node:fs";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
const LOCK_PATH: string = process.env.GLUDD_COMMIT_LOCK_PATH || "/tmp/gludd-commit.lock";
export const STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes
export const COMMIT_TARGETS = Object.freeze([
  "git-commit",
  "commit-no-verify",
  "git-commit-no-verify",
  "ship-commit",
  "repo-commit",
  "git-commit-file",
  "test-and-commit",
  "commit-bootstrap",
  "git-amend-msg",
]) as readonly string[];
export const DENY_MESSAGE =
  "COMMIT-LOCK: another commit is in flight. Parallel commits race on the git index " +
  "(AGENTS.md commit-serialization guardrail). Retry serially — dispatch ONE commit " +
  "subagent at a time, or use `make ship-commit-files FILES='...'` for atomic staging. " +
  "Set GLUDD_COMMIT_LOCK_ENFORCE=0 to disable.";
let _heldByThisCall = false;
export function isCommitCommand(cmd: string): boolean {
  for (const target of COMMIT_TARGETS) {
    const escaped = target.replace(/[-]/g, "\\-");
    // Match `make <target>` followed by whitespace or end-of-string.
    // The `(?:\s|$)` ensures `ship-commit` does NOT match `ship-commit-files`.
    const re = new RegExp(`\\bmake\\s+${escaped}(?:\\s|$)`);
    if (re.test(cmd)) return true;
  }
  return false;
}
export function lockAge(): number {
  try {
    const stat = fs.statSync(LOCK_PATH);
    return Date.now() - stat.mtimeMs;
  } catch {
    return -1;
  }
}
export function tryAcquire(): boolean {
  try {
    const fd = fs.openSync(LOCK_PATH, "wx");
    fs.writeSync(fd, String(process.pid));
    fs.closeSync(fd);
    return true;
  } catch {
    return false;
  }
}
export function releaseLock(): void {
  try {
    fs.unlinkSync(LOCK_PATH);
  } catch {
    // ignore — file may not exist
  }
}
export default function commitLockPlugin(api: Plugin): void {
  api.tool.execute.before((params) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (process.env.OPENCODE_SUBAGENT === "1") return
    if (isSubagent()) return
    reportAlive("enforce-commit-lock");
    _heldByThisCall = false;
    try {
      if (process.env.GLUDD_COMMIT_LOCK_ENFORCE === "0") return;
      if (params.tool !== "bash") return;
      const cmd: string = (params as { command?: string }).command ?? "";
      if (!isCommitCommand(cmd)) return;
      // Try to acquire the lock.
      if (tryAcquire()) {
        _heldByThisCall = true;
        return;
      }
      // Lock exists — check staleness.
      const age = lockAge();
      if (age > STALE_THRESHOLD_MS) {
        releaseLock();
        if (tryAcquire()) {
          _heldByThisCall = true;
          return;
        }
      }
      // Lock held by another commit in flight. DENY.
      return {
        permissionDecision: "deny" as const,
        message: DENY_MESSAGE,
      };
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  });
  api.tool.execute.after((params) => {
    try {
      if (process.env.GLUDD_COMMIT_LOCK_ENFORCE === "0") return;
      if (!_heldByThisCall) return;
      if (params.tool !== "bash") return;
      releaseLock();
      _heldByThisCall = false;
    } catch {
      // Fail-open
    }
  });
}
