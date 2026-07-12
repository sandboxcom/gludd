import type { Plugin } from "@opencode/core";

interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
}

interface EditArgs {
  file_path: string;
  old_string: string;
  new_string: string;
}

interface WriteArgs {
  file_path: string;
  content: string;
}

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

function formatBashBlockedMessage(message: string): string {
  return `\n⛔ BASH BLOCKED: ${message}\n\nThis is a guardrail. Set DELETION_REASON=<reason> env var to proceed.\n`;
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
    const fs = await import("node:fs/promises");
    const content = await fs.readFile(filePath, "utf-8");
    return countLines(content);
  } catch {
    return 0;
  }
}

async function appendAuditLog(entry: DeletionAuditEntry): Promise<void> {
  try {
    const fs = await import("node:fs/promises");
    const logLine = `${entry.timestamp} | ${entry.file} | lines_removed=${entry.lines_removed} | reason="${entry.reason}"\n`;
    await fs.appendFile(".deletion-audit.log", logLine);
  } catch {
    // Fail silently - audit logging should not block operations
  }
}

function getDeletionThreshold(): number {
  const envThreshold = process.env.GLUDD_DELETION_GATE_THRESHOLD;
  if (envThreshold !== undefined) {
    const parsed = parseInt(envThreshold, 10);
    if (!Number.isNaN(parsed) && parsed >= 0) {
      return parsed;
    }
  }
  return 5;
}

function getDeletionReason(): string | undefined {
  const reason = process.env.DELETION_REASON;
  return reason && reason.trim().length > 0 ? reason.trim() : undefined;
}

async function _reportAlive(): Promise<void> {
  try {
    const fs = await import("node:fs");
    const aliveFile = "/tmp/gludd-plugin-alive.json";
    let alive: Record<string, unknown> = {};
    try { alive = JSON.parse(fs.readFileSync(aliveFile, "utf-8")); } catch { /* ok */ }
    alive["enforce-deletion-gate"] = { last_seen: Date.now() };
    fs.writeFileSync(aliveFile, JSON.stringify(alive));
  } catch { /* fail-open */ }
}

const plugin: Plugin = {
  name: "enforce-deletion-gate",
  version: "1.0.0",
  hooks: {
    "tool.execute.before": async (toolCall: ToolCall) => {
      if (process.env.OPENCODE_SUBAGENT === "1") return
      await _reportAlive();
      const threshold = getDeletionThreshold();
      if (threshold <= 0) {
        return; // Gate disabled
      }

      let filePath: string | undefined;
      let linesRemoved = 0;

      if (toolCall.tool === "edit") {
        const args = toolCall.args as EditArgs;
        filePath = args.file_path;
        const oldLines = countLines(args.old_string);
        const newLines = countLines(args.new_string);
        linesRemoved = Math.max(0, oldLines - newLines);
      } else if (toolCall.tool === "write") {
        const args = toolCall.args as WriteArgs;
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
          throw new Error(
            formatBashBlockedMessage(
              formatThresholdExceededMessage(linesRemoved, threshold, filePath || "unknown"),
            ),
          );
        }

        await appendAuditLog({
          timestamp: new Date().toISOString(),
          file: filePath || "unknown",
          lines_removed: linesRemoved,
          reason,
        });
      }
    },
  },
};

export default plugin;
