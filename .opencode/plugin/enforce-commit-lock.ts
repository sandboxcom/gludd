// Per AGENTS.md commit-serialization guardrail (Wave 12-14 incidents): multiple
// Default ON. Fail-open: any throw/exception → allow (don't wedge the editor).
import * as fs from "node:fs";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
import { isCommitCommand } from "../lib/plugin_test_exports.ts";

const LOCK_PATH: string = process.env.GLUDD_COMMIT_LOCK_PATH || "/tmp/gludd-commit.lock";
const STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes
const DENY_MESSAGE =
  "COMMIT-LOCK: another commit is in flight. Parallel commits race on the git index " +
  "(AGENTS.md commit-serialization guardrail). Retry serially — dispatch ONE commit " +
  "subagent at a time, or use `make ship-commit-files FILES='...'` for atomic staging. " +
  "Set GLUDD_COMMIT_LOCK_ENFORCE=0 to disable.";
let _heldByThisCall = false;

function lockAge(): number {
  try { const stat = fs.statSync(LOCK_PATH); return Date.now() - stat.mtimeMs; } catch { return -1; }
}
function tryAcquire(): boolean {
  try {
    const fd = fs.openSync(LOCK_PATH, "wx");
    fs.writeSync(fd, String(process.pid)); fs.closeSync(fd);
    return true;
  } catch { return false; }
}
function releaseLock(): void { try { fs.unlinkSync(LOCK_PATH) } catch {} }

async function beforeHook(input: { tool: string }): Promise<{permissionDecision: string, message: string} | undefined> {
  if (isSubagent()) return
  reportAlive("enforce-commit-lock")
  _heldByThisCall = false
  try {
    if (process.env.GLUDD_COMMIT_LOCK_ENFORCE === "0") return
    if (input.tool !== "bash") return
    const params = input as { tool: string; command?: string }
    const cmd: string = params.command ?? ""
    if (!isCommitCommand(cmd)) return
    if (tryAcquire()) { _heldByThisCall = true; return }
    const age = lockAge()
    if (age > STALE_THRESHOLD_MS) {
      releaseLock()
      if (tryAcquire()) { _heldByThisCall = true; return }
    }
    return { permissionDecision: "deny", message: DENY_MESSAGE }
  } catch {
    // Fail-open: never wedge the editor on a plugin error.
  }
}

async function afterHook(input: { tool: string }): Promise<void> {
  try {
    if (process.env.GLUDD_COMMIT_LOCK_ENFORCE === "0") return
    if (!_heldByThisCall) return
    if (input.tool !== "bash") return
    releaseLock(); _heldByThisCall = false
  } catch {}
}

export default async function commitLockPlugin(
  _input: unknown,
  _options?: unknown,
): Promise<{
  "tool.execute.before": (input: { tool: string }, output: unknown) => Promise<{permissionDecision: string, message: string} | undefined>
  "tool.execute.after": (input: { tool: string }, output: unknown) => Promise<void>
}> {
  return { "tool.execute.before": beforeHook, "tool.execute.after": afterHook }
}
