/**
 * enforce-audit.ts — on session end (text.complete), verify that
 * TASKS.md items are ticked with evidence (commit hash, test count).
 * Enforces the Self-Audit policy from spec group A.
 *
 * Per AGENTS.md "Completion = Green Gate + TASKS.md Evidence": a task
 * may be called complete ONLY when TASKS.md has the item ticked with
 * evidence. Unchecked items without evidence are flagged.
 *
 * Mechanism:
 *   - `text.complete`: scans TASKS.md for unchecked items (`- [ ]`).
 *     If unchecked items exist and the outgoing text contains done-words
 *     without evidence, injects a warning directive.
 *   - Also checks ratchet.yml for known-unfixed work.
 *   - Fail-open on any file-read error.
 *
 * Env knobs:
 *   GLUDD_AUDIT_ENFORCE=0  — disable (no-op)
 *
 * Default ON. Fail-open: any throw/exception → allow.
 *
 * HOT-RELOAD: implements the proxy pattern from hot_reload.ts.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import type { Plugin } from "@opencode-ai/plugin";
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts";
import { isSubagent, reportAlive, getProjectRoot } from "../lib/shared.ts";

// Real path from project root cache (gracefully degrades to "").
const PROJECT_ROOT = getProjectRoot();

export function readTasksMd(): string {
  try {
    const p = path.join(PROJECT_ROOT, "TASKS.md");
    if (fs.existsSync(p)) return fs.readFileSync(p, "utf8");
  } catch {}
  return "";
}

export function readRatchetYml(): string {
  try {
    const p = path.join(PROJECT_ROOT, "config", "ratchet.yml");
    if (fs.existsSync(p)) return fs.readFileSync(p, "utf8");
  } catch {}
  return "";
}

export function hasUncheckedTasks(md: string): boolean {
  return /^\s*-\s+\[ \]/.test(md);
}

export function hasRatchetEntries(yml: string): boolean {
  const trimmed = yml.trim();
  if (!trimmed) return false;
  const entries = trimmed.split("\n").filter(
    (l) => l.trim() && !l.trim().startsWith("#")
  );
  return entries.length > 1;
}

export const DONE_WORDS_RE =
  /\b(landed|committed|pushed|fixed|passing|shipped|done|complete|green|resolved|deployed|verified|passed|working)\b/i;

export const EVIDENCE_RE =
  /\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b|VERIFIED\s+\S+@[0-9a-f]+|CI\s+(GREEN|RED|PENDING)|\d+\s+passed|=== GATE:\s+PASSED\s+===/;

const defaultImpl: HotModule = {
  "text.complete": async (_output) => {
    if (isSubagent()) return;
    reportAlive("enforce-audit");
    try {
      if (process.env.GLUDD_AUDIT_ENFORCE === "0") return;

      const tasks = readTasksMd();
      const ratchet = readRatchetYml();
      const unchecked = hasUncheckedTasks(tasks);
      const ratchetSet = hasRatchetEntries(ratchet);

      if (!unchecked && !ratchetSet) return;

      // Check the current output text for done-words without evidence
      const out = _output as { text?: string };
      const text = out?.text ?? "";
      const hasDoneWord = DONE_WORDS_RE.test(text);
      const hasEvidence = EVIDENCE_RE.test(text);

      if (hasDoneWord && !hasEvidence) {
        const uncheckedCount = (tasks.match(/^[ \t]*-[ \t]+\[[^\]]\]/gm) || []).length;
        return {
          text:
            "AUDIT REQUIRED: " +
            `${uncheckedCount} unchecked TASKS.md item(s) remain. ` +
            (ratchetSet ? "config/ratchet.yml has known-unfixed entries. " : "") +
            "Claims of completion require evidence (commit hash, test counts, CI verdict). " +
            "Tick items with evidence before declaring done. " +
            "Set GLUDD_AUDIT_ENFORCE=0 to disable.",
        };
      }
    } catch {
      // Fail-open
    }
  },
};

export default (async ({}) => {
  return {
    "text.complete": async (output) => {
      if (isSubagent()) return;
      const impl = loadHotModule("audit", defaultImpl);
      const fn = impl["text.complete"];
      return fn ? await fn(output) : undefined;
    },
  };
}) satisfies Plugin;
