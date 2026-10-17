// No idle monitoring while work remains.
// Tracks consecutive read-only monitoring tool calls and denies further monitoring
// after a threshold unless a productive action resets the counter.
// Fail-open: any throw/exception -> allow.

import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
import { idleMonitoringToolExecuteBefore, type IdleMonitoringConfig } from "../lib/idle_monitoring.ts";

const ENFORCE = process.env.GLUDD_NO_IDLE_MONITORING_ENFORCE !== "0";

const THRESHOLD = 3;

const MONITORING_PATTERNS: readonly RegExp[] = Object.freeze([
  /\bmake\s+gate-status-check\b/, /\bmake\s+gate-progress\b/, /\bmake\s+gate-logs\b/,
  /\bmake\s+gate-tail\b/, /\bmake\s+gate-wait-report\b/, /\bmake\s+ci-status\b/,
  /\bmake\s+ci-verdict-fast\b/, /\bmake\s+ci-cooldown-status\b/, /\bmake\s+git-status\b/,
  /\bmake\s+agent-worktree-list\b/, /\bmake\s+worktree-health-check\b/,
]) as readonly RegExp[];

const DENY_MESSAGE = "Idle monitoring blocked (AGENTS.md 'No Idle Monitoring While Work Remains'). You have checked status repeatedly without doing productive work. Do one of: edit/write a file, run a mutating make target, dispatch a subagent, or work in a git worktree on the next independent fix. Set GLUDD_NO_IDLE_MONITORING_ENFORCE=0 to disable.";

const CONFIG: IdleMonitoringConfig = {
  stateFile: process.env.GLUDD_IDLE_MONITORING_STATE_FILE || "/tmp/gludd-idle-monitoring.json", threshold: THRESHOLD, staleMs: 120_000, patterns: MONITORING_PATTERNS, productiveTools: Object.freeze(["edit", "write", "task", "agent", "workflow"]), envDisableVar: "GLUDD_NO_IDLE_MONITORING_ENFORCE", pluginName: "enforce-no-idle-monitoring",
};

const defaultImpl = {
  "tool.execute.before": async (input: unknown, output: unknown) => {
    if (isSubagent()) return;
    reportAlive("enforce-no-idle-monitoring");
    if (!ENFORCE) return;
    try { return idleMonitoringToolExecuteBefore(input as {tool?: string}, output, CONFIG, DENY_MESSAGE); } catch {}
  },
};

export default (({ }) => ({
  "tool.execute.before": async (input, output) => {
    if (isSubagent()) return;
    reportAlive("enforce-no-idle-monitoring");
    if (!ENFORCE) return;
    const impl = loadHotModule("enforce-no-idle-monitoring", defaultImpl);
    return impl["tool.execute.before"]?.(input, output);
  },
})) satisfies Plugin;
