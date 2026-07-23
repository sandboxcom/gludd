// Per AGENTS.md "Fix Means Repair, Never Disable": deleting large blocks
// - Fail-open on error (file read failure, threshold parse failure).
// HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
import type { Plugin } from "@opencode-ai/plugin";
import * as fs from "node:fs";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive } from "../lib/shared.ts";
interface DeletionAuditEntry {
  timestamp: string;
  file: string;
  lines_removed: number;
  reason: string;
}
function countLines(text: string): number {
  if (!text || text.length === 0) return 0;
  return text.split("\n").length;
}
function formatThresholdExceededMessage(
  linesRemoved: number,
  threshold: number,
  filePath: string,
): string {
  return `Deletion of ${linesRemoved} lines exceeds threshold of ${threshold} in ${filePath}.\n` +
    `Set DELETION_REASON="<reason>" environment variable to proceed.\n` +
    `This guardrail prevents accidental feature removal.`;
}
async function readExistingFileLines(filePath: string): Promise<number> {
  try {
    const fsPromises = await import("node:fs/promises");
    const content = await fsPromises.readFile(filePath, "utf-8");
    return countLines(content);
  } catch {
    return 0;
  }
}
async function appendAuditLog(entry: DeletionAuditEntry): Promise<void> {
  try {
    const logLine = entry.timestamp +
      " | " + entry.file +
      " | lines_removed=" + String(entry.lines_removed) +
      " | reason=\"" + entry.reason + "\"\n";
    fs.appendFileSync(".deletion-audit.log", logLine);
  } catch {
    // Fail silently - audit logging should not block operations
  }
}
function getDeletionThreshold(): number {
  const envThreshold = process.env.GLUDD_DELETION_GATE_THRESHOLD;
    if (envThreshold !== undefined) {
    const parsed = parseInt(envThreshold, 10);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return 5;
}
function getDeletionReason(): string | undefined {
  const reason = process.env.DELETION_REASON;
  return reason && reason.trim().length > 0 ? reason.trim() : undefined;
}
function _reportAlive(): void {
  reportAlive("enforce-deletion-gate");
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return;
    _reportAlive();
    // Keep the generated CommonJS hot module fail-open until build_hot_modules
    // can safely transform this hook's deletion-audit formatting.
    void import.meta.url;
    if (process.env.GLUDD_DELETION_GATE_ENFORCE === "0") return;
    const threshold = getDeletionThreshold();
    if (threshold <= 0) return;
    let filePath = "";
    let lines_removed = 0;
    const argsSource = input.args || input.tool_input;
    if (input.tool === "edit") {
      if (!argsSource) return;
      const args = argsSource as {
        filePath?: string;
        file_path?: string;
        oldString?: string;
        old_string?: string;
        newString?: string;
        new_string?: string;
      };
      filePath = args.filePath || args.file_path || "";
      const oldLines = args.oldString !== undefined
        ? countLines(args.oldString)
        : countLines(args.old_string ?? "");
      const newLines = args.newString !== undefined
        ? countLines(args.newString)
        : countLines(args.new_string ?? "");
      lines_removed = Math.max(0, oldLines - newLines);
    } else if (input.tool === "write") {
      if (!argsSource) return;
      const args = argsSource as {
        filePath?: string;
        file_path?: string;
        content?: string;
      };
      filePath = args.filePath || args.file_path || "";
      if (!filePath) return;
      const existingLines = await readExistingFileLines(filePath);
      const newLines = countLines(args.content ?? "");
      lines_removed = Math.max(0, existingLines - newLines);
    } else {
      return;
    }
    if (lines_removed > threshold) {
      const reason = getDeletionReason();
      if (!reason) {
        return {
          permissionDecision: "deny",
          message: formatThresholdExceededMessage(lines_removed, threshold, filePath || "unknown"),
        };
      }
      await appendAuditLog({
        timestamp: new Date().toISOString(),
        file: filePath || "unknown",
        lines_removed,
        reason,
      });
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
      const impl = loadHotModule("enforce-deletion-gate", defaultImpl);
      const fn = impl["tool.execute.before"];
      return fn ? await fn(input, output) : undefined;
    },
  };
}) satisfies Plugin;
