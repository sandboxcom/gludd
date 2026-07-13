/**
 * enforce-no-wait.ts — deny main-thread sleeps/waits/tails AND CI-poll
 * dispatches while real work could be running.
 *
 * Two enforcement surfaces:
 *
 * 1. Main-thread bash waits (per AGENTS.md "Background Operations NEVER Block
 *    Dispatch", 2026-07-06): the main thread must dispatch subagents and poll
 *    — never sleep. A background gate is NOT a blocker for any other work.
 *    Denied patterns (main-thread bash only; Task-dispatched bash is unaffected):
 *      - `sleep N && make ...`            (sleep-then-poll anti-pattern)
 *      - `sleep N`                        (any naked sleep on the main thread)
 *      - `make gate-tail`                 (follows forever, blocks dispatch)
 *      - `make gate-status-check`         (when called directly, not via Task)
 *      - `make gate-bg-check`             (same)
 *
 * 2. CI-poll dispatch intent (per AGENTS.md "CI-Poll Subagents Are Forbidden"
 *    + "Machine-Enforced CI Check Cooldown", 2026-07-08): a subagent whose job
 *    is "poll CI until terminal / wait for CI green" holds a floor slot for
 *    30–40 min producing zero value. CI runs on its own schedule; polling
 *    doesn't speed it up. The runtime cooldown (`make ci-verdict-safe`)
 *    prevents the make-side loop; this matcher blocks the dispatch intent at
 *    the source. Applied to the `prompt`/`description` of Task/agent/workflow.
 *
 * Default ON. Set GLUDD_NO_WAIT_ENFORCE=0 to disable (advisory only).
 * Fail-open: any throw/exception → allow (don't wedge the editor).
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-enforce-no-wait.js on every invocation.  If present
 * and newer than cached, the hot module's hook overrides the compiled-in
 * default.  Run `make hot-reload-plugins` after editing this file.
 */
import * as fs from "node:fs";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";

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

export const CI_POLL_DISPATCH_PATTERNS: readonly RegExp[] = Object.freeze([
  /\bpoll\s+CI\s+until\b/i,
  /\bpoll(?:ing)?\s+(?:for\s+)?CI\s+(?:status\s+)?until\b/i,
  /\bwait\s+for\s+CI\s+(?:to\s+)?(?:turn\s+|go\s+|become\s+)?green\b/i,
  /\bwait\s+until\s+CI\s+(?:is\s+)?green\b/i,
  /\bloop\s+(?:on\s+)?make\s+ci-verdict\b/i,
  /\bevery\s+\d+\s+seconds?[\s\S]{0,200}?\b(?:up\s+to|iterations?|until)\b/i,
  /\buntil\s+conclusion\s+(?:is\s+)?success\b/i,
]) as readonly RegExp[];

export const CI_POLL_DENY_MESSAGE =
  "CI-poll dispatch forbidden (AGENTS.md 'CI-Poll Subagents Are Forbidden' + " +
  "'Machine-Enforced CI Check Cooldown'). A subagent that polls CI until terminal " +
  "burns a floor slot for 30+ minutes producing zero value. CI runs on its own " +
  "schedule; the only thing that finishes it is wall-clock time. DISPATCH real " +
  "work instead; check CI at the next natural break with `make ci-verdict-safe` " +
  "(10-min cooldown enforced). `make ci-wait` is for release-cut ONLY.";

const DISPATCH_TOOLS = new Set(["task", "agent", "workflow"]);

function _extractDispatchText(params: unknown): string {
  const p = params as {
    prompt?: unknown;
    description?: unknown;
    input?: { prompt?: unknown; description?: unknown };
  };
  const parts: string[] = [];
  if (typeof p.prompt === "string") parts.push(p.prompt);
  if (typeof p.description === "string") parts.push(p.description);
  if (p.input && typeof p.input === "object") {
    if (typeof p.input.prompt === "string") parts.push(p.input.prompt);
    if (typeof p.input.description === "string") parts.push(p.input.description);
  }
  return parts.join("\n");
}

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return;
    reportAlive("enforce-no-wait");
    try {
      if (process.env.GLUDD_NO_WAIT_ENFORCE === "0") return;

      if (input.tool === "bash") {
        const cmd: string = input.command ?? "";
        if (cmd) {
          for (const pattern of WAIT_PATTERNS) {
            if (pattern.test(cmd)) {
              return {
                permissionDecision: "deny" as const,
                message: DENY_MESSAGE,
              };
            }
          }
        }
        return;
      }

      if (DISPATCH_TOOLS.has(input.tool)) {
        const text = _extractDispatchText(input);
        if (text) {
          for (const pattern of CI_POLL_DISPATCH_PATTERNS) {
            if (pattern.test(text)) {
              return {
                permissionDecision: "deny" as const,
                message: CI_POLL_DENY_MESSAGE,
              };
            }
          }
        }
      }
    } catch {
      // Fail-open: never wedge the editor on a plugin error.
    }
  },
};

// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return;
      const impl = loadHotModule("enforce-no-wait", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
