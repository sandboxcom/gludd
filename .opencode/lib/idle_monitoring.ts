import * as fs from "node:fs";
import { extractBashCommand, reportAlive } from "./shared.ts";

export interface IdleMonitoringConfig {
  stateFile: string;
  threshold: number;
  staleMs: number;
  patterns: readonly RegExp[];
  productiveTools: readonly string[];
  envDisableVar: string;
  pluginName: string;
}

interface IdleState {
  count: number;
  last_ts: number;
}

export const DEFAULT_IDLE_DENY_MESSAGE =
  "Idle monitoring blocked (AGENTS.md 'No Idle Monitoring While Work Remains'). " +
  "You have checked status repeatedly without doing productive work. " +
  "Do one of: edit/write a file, run a mutating make target, dispatch a subagent, " +
  "or work in a git worktree on the next independent fix.";

function readState(cfg: IdleMonitoringConfig): IdleState {
  try {
    if (fs.existsSync(cfg.stateFile)) {
      const raw = JSON.parse(fs.readFileSync(cfg.stateFile, "utf8")) as IdleState;
      if (Date.now() - raw.last_ts > cfg.staleMs) {
        return { count: 0, last_ts: Date.now() };
      }
      return raw;
    }
  } catch { /* fail-open */ }
  return { count: 0, last_ts: Date.now() };
}

function writeState(cfg: IdleMonitoringConfig, s: IdleState): void {
  try {
    fs.writeFileSync(cfg.stateFile, JSON.stringify(s), "utf8");
  } catch { /* fail-open */ }
}

function isMonitoringCommand(cfg: IdleMonitoringConfig, cmd: string): boolean {
  return cfg.patterns.some((p) => p.test(cmd));
}

function isProductiveTool(cfg: IdleMonitoringConfig, tool: string): boolean {
  return cfg.productiveTools.includes(tool);
}

export function idleMonitoringToolExecuteBefore(
  input: { tool?: string },
  output: unknown,
  cfg: IdleMonitoringConfig,
  denyMessage: string,
): { permissionDecision: "deny"; message: string } | undefined {
  reportAlive(cfg.pluginName);
  try {
    if (process.env[cfg.envDisableVar] === "0") return undefined;
    const tool = input.tool;
    const state = readState(cfg);
    if (tool === "bash") {
      const cmd = extractBashCommand(input, output);
      if (cmd && isMonitoringCommand(cfg, cmd)) {
        if (state.count >= cfg.threshold - 1) {
          return { permissionDecision: "deny" as const, message: denyMessage };
        }
        state.count += 1;
        state.last_ts = Date.now();
        writeState(cfg, state);
        return undefined;
      }
    }
    if (tool && isProductiveTool(cfg, tool)) {
      writeState(cfg, { count: 0, last_ts: Date.now() });
      return undefined;
    }
    return undefined;
  } catch {
    // Fail-open: never wedge the editor on a plugin error.
    return undefined;
  }
}
