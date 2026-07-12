/**
 * enforce-commit-lock.ts — serialize commit-shaped make targets so parallel
 * subagents cannot race on the git index (staging sweeps, index lock errors).
 *
 * Per AGENTS.md commit-serialization guardrail (Wave 12-14 incidents): multiple
 * parallel subagents running `make ship-commit` caused `git add -A` to sweep
 * another's staged files, producing misattributed commits. This plugin is
 * LAYER 2 of the guardrail:
 *
 *   LAYER 1 — Makefile `_commit-lock-acquire` flock wrapper (per-recipe lock)
 *   LAYER 2 — this plugin: O_EXCL create on the lock file wrapping the ENTIRE
 *             bash tool call boundary (the real serialization mechanism)
 *
 * Mechanism:
 *   - `tool.execute.before`: if the bash command invokes a commit-shaped make
 *     target, attempt O_EXCL create on the lock file. Success = lock acquired
 *     (held until tool.execute.after). Failure = another commit is in flight:
 *     check staleness (> STALE_THRESHOLD_MS → break + retry); otherwise DENY.
 *   - `tool.execute.after`: remove the lock file (release).
 *
 * Env knobs:
 *   GLUDD_COMMIT_LOCK_ENFORCE=0  — disable (no-op)
 *   GLUDD_COMMIT_LOCK_PATH=...   — override lock file path
 *
 * Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
 */
import * as fs from "fs";
import type { PluginAPI } from "@opencode/plugin";

/** Lock file path (overridable via GLUDD_COMMIT_LOCK_PATH). */
const LOCK_PATH: string = process.env.GLUDD_COMMIT_LOCK_PATH || "/tmp/gludd-commit.lock";

/** Stale-break threshold: a lock older than this is considered dead (ms). */
export const STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes

/** Commit-shaped make targets that must be serialized. */
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

/** Tracks whether the CURRENT tool call holds the lock (for release in after). */
let _heldByThisCall = false;

function _reportAlive(): void {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json";
    const alive = fs.existsSync(alivePath)
      ? (JSON.parse(fs.readFileSync(alivePath, "utf8")) as Record<string, unknown>)
      : {};
    alive["enforce-commit-lock"] = { last_seen: Date.now() };
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8");
  } catch {
    // fail-open
  }
}

/** Returns true if the bash command invokes a commit-shaped make target. */
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

/** Returns the age of the lock file in ms, or -1 if it does not exist. */
export function lockAge(): number {
  try {
    const stat = fs.statSync(LOCK_PATH);
    return Date.now() - stat.mtimeMs;
  } catch {
    return -1;
  }
}

/** O_EXCL create: succeeds only if the file does NOT already exist. */
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

/** Remove the lock file (stale-break or release). */
export function releaseLock(): void {
  try {
    fs.unlinkSync(LOCK_PATH);
  } catch {
    // ignore — file may not exist
  }
}

export default function commitLockPlugin(api: PluginAPI): void {
  api.tool.execute.before((params) => {
    if (process.env.OPENCODE_SUBAGENT === "1") return
    _reportAlive();
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
