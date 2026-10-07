// Per AGENTS.md "Session Persistence Policy": SESSION.md must be read at
// - Missing SESSION.md or stat failure → allow.
// - Corrupt cache state → force a fresh SESSION.md check (fail-closed).
// Default ON. Unexpected hook exceptions remain fail-open to avoid wedging the TUI.
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
import * as fs from "node:fs";
import * as path from "node:path";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, isReadTool, reportAlive, readJsonFile, writeJsonFile, getProjectRoot } from "../lib/shared.ts";
const STATE_FILE = process.env.GLUDD_CONTEXT_STATE_FILE || "/tmp/gludd-context-check.json";
const DEFAULT_STALE_SECONDS = 86400;
const PROJECT_ROOT = getProjectRoot();
const SESSION_FILE = process.env.GLUDD_CONTEXT_SESSION_FILE || path.join(PROJECT_ROOT, "SESSION.md");
interface ContextCheckState {
  lastCheckedEpoch: number;
  sessionPid: number;
}
function getStaleSeconds(): number {
  const val = process.env.GLUDD_CONTEXT_STALE_SECONDS;
  if (val) {
    const n = parseInt(val, 10);
    if (!isNaN(n) && n > 0) return n;
  }
  return DEFAULT_STALE_SECONDS;
}
function getSessionMdMtime(): number | null {
  try {
    if (fs.existsSync(SESSION_FILE)) return Math.floor(fs.statSync(SESSION_FILE).mtimeMs / 1000);
  } catch {}
  return null;
}
function isStale(mtimeSec: number, thresholdSec: number): boolean {
  const now = Math.floor(Date.now() / 1000);
  return now - mtimeSec > thresholdSec;
}
function shouldCheck(state: ContextCheckState): boolean {
  const now = Math.floor(Date.now() / 1000);
  // Check on PID change (new session) or if never checked
  if (state.sessionPid !== process.pid) return true;
  // Re-check every 6 hours even in same session
  return now - state.lastCheckedEpoch > 21600;
}
function loadState(): ContextCheckState {
  // readJsonFile's default deliberately represents "never checked". A corrupt
  // cache therefore cannot suppress the stale-session validation.
  return readJsonFile<ContextCheckState>(STATE_FILE, {
    lastCheckedEpoch: 0,
    sessionPid: 0,
  });
}
function saveState(s: ContextCheckState): void {
  writeJsonFile(STATE_FILE, s);
}
const defaultImpl: HotModule = {
  "tool.execute.before": async (_input, _output) => {
    if (isSubagent()) return;
    reportAlive("enforce-context");
    try {
      if (process.env.GLUDD_CONTEXT_ENFORCE === "0") return;
      const state = loadState();
      if (!shouldCheck(state)) return;
      if (isReadTool((_input as any)?.tool ?? "")) return;
      const staleSec = getStaleSeconds();
      const mtime = getSessionMdMtime();
      if (mtime !== null && isStale(mtime, staleSec)) {
        saveState({ lastCheckedEpoch: Math.floor(Date.now() / 1000), sessionPid: process.pid });
        return {
          permissionDecision: "deny",
          message:
            `CONTEXT: SESSION.md is stale (>${Math.round(staleSec / 3600)}h since update). ` +
            `Read SESSION.md to restore context from prior sessions. ` +
            `Update SESSION.md after this session's work is committed. ` +
            `Set GLUDD_CONTEXT_ENFORCE=0 to disable.`,
        };
      }
      saveState({ lastCheckedEpoch: Math.floor(Date.now() / 1000), sessionPid: process.pid });
    } catch {
      // Fail-open
    }
  },
};
export default (async ({}) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("context", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
