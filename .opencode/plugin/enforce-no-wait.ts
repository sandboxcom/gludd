/**
 * enforce-no-wait.ts — deny main-thread sleeps/waits/tails while a background
 * operation could be running. Per AGENTS.md "Background Operations NEVER Block
 * Dispatch" (added 2026-07-06): the main thread must dispatch subagents and
 * poll — never sleep. A background gate is NOT a blocker for any other work.
 *
 * Denied patterns (main-thread bash only; Task-dispatched bash is unaffected):
 *   - `sleep N && make ...`            (sleep-then-poll anti-pattern)
 *   - `sleep N`                        (any naked sleep on the main thread)
 *   - `make gate-tail`                 (follows forever, blocks dispatch)
 *   - `make gate-status-check`         (when called directly, not via Task)
 *   - `make gate-bg-check`             (same)
 *
 * Default ON. Set GLUDD_NO_WAIT_ENFORCE=0 to disable (advisory only).
 * Fail-open: any throw/exception → allow (don't wedge the editor).
 *
 * The plugin cannot distinguish "main thread" from "subagent" mechanically —
 * opencode exposes no caller-context API. Instead it denies these patterns
 * unconditionally; subagents that legitimately need to poll should do so via
 * a `while` loop with short sleeps inside a Task, not via shell `sleep && ...`.
 * (Subagents inherit the plugin; the workaround is `for i in range(N): sleep(1)`
 * inside the Task prompt, not a shell-level `sleep && make`.)
 */
import * as fs from "fs";
import type { PluginAPI } from "@opencode/plugin";

function _reportAlive() {
  try {
    const alivePath = "/tmp/gludd-plugin-alive.json";
    const alive = fs.existsSync(alivePath)
      ? JSON.parse(fs.readFileSync(alivePath, "utf8"))
      : {};
    alive["enforce-no-wait"] = { last_seen: Date.now() };
    fs.writeFileSync(alivePath, JSON.stringify(alive), "utf8");
  } catch {
    // fail-open
  }
}

export const WAIT_PATTERNS: readonly RegExp[] = Object.freeze([
  /\bsleep\s+\d+\s*&&\s*make\b/,
  /\bsleep\s+\d+\s*$/,
  /\bmake\s+gate-tail\b/,
  /\bmake\s+gate-bg-check\b/,
  /\bmake\s+gate-status-check\b/,
]) as readonly RegExp[];

export const DENY_MESSAGE =
  "Main-thread wait forbidden (AGENTS.md 'Background Operations NEVER Block Dispatch'). " +
  "Background ops are NOT a blocker for other work. DISPATCH subagents now; poll the gate via a Task tool call, not via shell sleep. " +
  "Set GLUDD_NO_WAIT_ENFORCE=0 to disable.";

export default function noWaitPlugin(api: PluginAPI): void {
  api.tool.execute.before((params) => {
    _reportAlive();
    try {
      if (process.env.GLUDD_NO_WAIT_ENFORCE === "0") return;
      if (params.tool !== "bash") return;
      const cmd: string = (params as { command?: string }).command ?? "";
      if (!cmd) return;
      for (const pattern of WAIT_PATTERNS) {
        if (pattern.test(cmd)) {
          return {
            permissionDecision: "deny" as const,
            message: DENY_MESSAGE,
          };
        }
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  });
}
