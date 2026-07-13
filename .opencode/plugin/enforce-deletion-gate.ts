/**
 * enforce-deletion-gate.ts — deny large deletions unless DELETION_REASON
 * env var is set, preventing accidental feature removal.
 *
 * Per AGENTS.md "Fix Means Repair, Never Disable": deleting large blocks
 * of code without a reason is classified as potential accidental deletion.
 *
 * Mechanism:
 *   - `tool.execute.before`: for edit/write tools, compute lines removed.
 *     If linesRemoved > threshold (default 5), require DELETION_REASON
 *     env var. If absent, deny with a message. If present, append to
 *     .deletion-audit.log and allow.
 *   - Fail-open on error (file read failure, threshold parse failure).
 *
 * Env knobs:
 *   GLUDD_DELETION_GATE_THRESHOLD=N — lines-removed threshold (default 5, 0 = disabled)
 *   DELETION_REASON="<reason>"      — reason string to allow a large deletion
 *   GLUDD_DELETION_GATE_ENFORCE=0   — disable entirely
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.  Hook functions
 * check /tmp/gludd-hot-enforce-deletion-gate.js on every invocation.  If present
 * and newer than cached, the hot module's hook overrides the compiled-in
 * default.  Run `make hot-reload-plugins` after editing this file.
 */
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
    const fsPromises = await import("node:fs/promises");
    const logLine = `${entry.timestamp} | ${entry.file} | lines_removed=${entry.lines_removed} | reason="${entry.reason}"\n`;
    await fsPromises.appendFile(".deletion-audit.log", logLine);
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

// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return;
    reportAlive("enforce-deletion-gate");

    if (process.env.GLUDD_DELETION_GATE_ENFORCE === "0") return;

    const threshold = getDeletionThreshold();
    if (threshold <= 0) return;

    let filePath: string | undefined;
    let linesRemoved = 0;

    if (input.tool === "edit") {
      if (!input.args) return;
      const args = input.args as { file_path: string; old_string: string; new_string: string };
      filePath = args.file_path;
      const oldLines = countLines(args.old_string);
      const newLines = countLines(args.new_string);
      linesRemoved = Math.max(0, oldLines - newLines);
    } else if (input.tool === "write") {
      if (!input.args) return;
      const args = input.args as { file_path: string; content: string };
      filePath = args.file_path;
      const existingLines = await readExistingFileLines(filePath);
      const newLines = countLines(args.content);
      linesRemoved = Math.max(0, existingLines - newLines);
    } else {
      return;
    }

    if (linesRemoved > threshold) {
      const reason = getDeletionReason();
      if (!reason) {
        return {
          permissionDecision: "deny",
          message: formatThresholdExceededMessage(linesRemoved, threshold, filePath || "unknown"),
        };
      }

      await appendAuditLog({
        timestamp: new Date().toISOString(),
        file: filePath || "unknown",
        lines_removed: linesRemoved,
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
