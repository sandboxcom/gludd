import { createRequire } from "node:module"

// Consolidated test-helper exports for enforcement plugins.
// These live OUTSIDE .opencode/plugin/ so opencode's auto-discovery
// loader (getLegacyPlugins) does not pick them up.
// Previously these were named exports in .opencode/plugin/*.ts files,
// which crashed the loader when it iterated Object.values(mod).

const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)
function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args)
}

// ── enforce-clean-tree helpers ──────────────────────────────────────────

export function getDispatchTools(): readonly string[] {
  return ["task", "agent", "workflow"]
}
export function getDenyMessagePrefix(): string { return "DIRTY TREE" }
export const METADATA_FILES: ReadonlySet<string> = Object.freeze(
  new Set(["SESSION.md", "TASKS.md", "BUGS.md", ".ci-status"]),
)
function _extractFilePath(line: string): string {
  const path = line.slice(3).trim()
  const arrow = path.lastIndexOf(" -> ")
  return arrow >= 0 ? path.slice(arrow + 4) : path
}
export function isMetadataOnlyDirty(status: string): boolean {
  if (!status.trim()) return true
  const lines = status.trim().split("\n").filter((l: string) => l.trim())
  if (lines.length === 0) return true
  return lines.every((line: string) => {
    const fp = _extractFilePath(line)
    return METADATA_FILES.has(fp)
  })
}
export function getGitStatus(): string {
  try {
    return execSync("git status --porcelain", {
      stdio: ["pipe", "pipe", "pipe"],
    }).toString().trim()
  } catch { return "" }
}
export function isTreeDirty(): boolean { return getGitStatus().length > 0 }
export function countDirtyFiles(status: string): number {
  if (!status.trim()) return 0
  return status.trim().split("\n").filter((l: string) => l.trim()).length
}
export function buildDenyMessage(count: number): string {
  return (
    "DIRTY TREE: " + count + " uncommitted file(s). Commit or stash before dispatching new work. " +
    "Run `make git-status` to see the files, then `make git-add FILES='...' && make ship-commit MSG='...'` to commit. " +
    "Or `make git-stash` to stash temporarily. Set GLUDD_CLEAN_TREE_ENFORCE=0 to disable."
  )
}

// ── enforce-commit-lock helpers ─────────────────────────────────────────

export const COMMIT_TARGETS = Object.freeze([
  "git-commit", "commit-no-verify", "git-commit-no-verify",
  "ship-commit", "repo-commit", "git-commit-file",
  "test-and-commit", "commit-bootstrap", "git-amend-msg",
]) as readonly string[]

export function isCommitCommand(cmd: string): boolean {
  for (const target of COMMIT_TARGETS) {
    const escaped = target.replace(/[-]/g, "\\-")
    if (new RegExp("\\bmake\\s+" + escaped + "(?:\\s|$)").test(cmd)) return true
  }
  return false
}

// ── enforce-no-suppressions helpers ─────────────────────────────────────

const SUPPRESSION_PATTERNS: RegExp[] = [
  /#\s*noqa/, /#\s*type:\s*ignore/, /#\s*pylint:/,
  /#\s*fmt:\s*(?:off|skip|on)/, /#\s*isort:\s*skip/,
]
const ALLOWLIST_PATHS: string[] = [
  "src/general_ludd/security/fix_not_disable.py",
  "tests/unit/test_type_safety_guardrails.py",
]

export function isSuppressionComment(text: string): boolean {
  if (typeof text !== "string" || text.length === 0) return false
  let quote: "'" | '"' | "'''" | '"""' | null = null
  let escaped = false
  for (let index = 0; index < text.length; index += 1) {
    if (quote !== null) {
      if (quote.length === 3) {
        if (text.startsWith(quote, index)) {
          quote = null
          index += 2
        }
        continue
      }
      const character = text[index]
      if (escaped) {
        escaped = false
      } else if (character === "\\") {
        escaped = true
      } else if (character === quote) {
        quote = null
      }
      continue
    }

    if (text.startsWith('"""', index) || text.startsWith("'''", index)) {
      quote = text.slice(index, index + 3) as "'''" | '"""'
      index += 2
      continue
    }
    const character = text[index]
    if (character === "'" || character === '"') {
      quote = character
      escaped = false
      continue
    }
    if (character === "#") {
      const newline = text.indexOf("\n", index)
      const end = newline === -1 ? text.length : newline
      if (SUPPRESSION_PATTERNS.some(re => re.test(text.slice(index, end)))) {
        return true
      }
      index = end
    }
  }
  return false
}
export function isAllowlistedPath(filePath: string): boolean {
  if (typeof filePath !== "string" || filePath.length === 0) return false
  return ALLOWLIST_PATHS.some(allowed => filePath.includes(allowed))
}
export function shouldAllowEdit(
  filePath: string, content: string,
): { allow: boolean; reason?: string } {
  try {
    if (isAllowlistedPath(filePath)) return { allow: true }
    if (isSuppressionComment(content)) {
      return { allow: false, reason: "Lint-suppression comments forbidden. Fix the underlying issue. See AGENTS.md Guardrail Integrity Policy." }
    }
    return { allow: true }
  } catch { return { allow: true } }
}

// ── enforce-stop helpers ────────────────────────────────────────────────

export function getSubagentDeficitRe(): RegExp {
  return /\b(?:agent|subagent|task)\s+\d+\s+(?:completed|finished|did|fixed|found|wrote|added|removed|updated|reported|returned|resolved|processed|handled|investigated|checked|audited|reviewed|implemented|created|tested|verified|deployed|patched|refactored|cleaned|merged|built|generated|produced|says|indicates|confirms|shows|began|started|noted)\b/i
}

export function getPermissionSeekingRe(): RegExp {
  return /(?:want me to|should i|shall i|^proceed\?)/im
}
export function getStatusSummaryRe(): RegExp {
  return new RegExp([
    "here.{0,4}s the (?:session\\s+\\d+\\s+)?(?:final\\s+)?status",
    "session\\s+\\d+\\s+(?:final\\s+)?(?:status|summary|wrap[- ]?up|recap)",
    "final (?:status|summary|state)(?:\\s+(?:report|summary))?\\b",
    "^\\s*#{1,4}\\s+.{0,40}(?:status|summary|recap)\\s*$",
    "status (?:report|summary|update)\\s*:",
  ].join("|"), "im")
}
export function looksLikeStatusSummary(text: string): boolean {
  if (!text || typeof text !== "string") return false
  const hasBold = /\*\*[^*]+\*\*/.test(text)
  const hasTable = /\|[^|]+\|[^|]+\|/.test(text)
  const bullets = (text.match(/^[ \t]*[-*][ \t]/gm) || []).length
  const hasHeader = /^#{1,4}\s+.+/.test(text)
  // Check the status-summary regex too
  const re = getStatusSummaryRe()
  return re.test(text) || (hasBold && (hasTable || bullets >= 2)) || (hasHeader && bullets >= 2)
}

// ── enforce-verified-claims helpers ─────────────────────────────────────

export const DONE_WORDS = [
  "landed", "committed", "pushed", "fixed", "passing",
  "shipped", "done", "complete", "green", "resolved",
  "deployed", "verified", "passed", "working",
] as const

export const EVIDENCE_PATTERNS: RegExp[] = [
  /\b[0-9a-f]*[a-f][0-9a-f]{6,39}\b/,
  /VERIFIED\s+\w+@/,
  /CI\s+(GREEN|RED|PENDING)/,
  /\d+\s+passed/,
  /===\s*(?:GATE|GATE-LITE):\s*(?:PASSED|FAILED)/,
  /Collection OK/,
  /All checks passed/,
  /Success: no issues found/,
]

export const NOT_DONE_PHRASES = [/\bworking\s+on\b/] as const

export function shouldBlock(text: string): boolean {
  if (!text || text.trim().length === 0) return false
  let lower = text.toLowerCase()
  for (const phrase of NOT_DONE_PHRASES) { lower = lower.replace(phrase, " ") }
  let found = false
  for (const w of DONE_WORDS) {
    if (new RegExp("\\b" + w + "\\b").test(lower)) { found = true; break }
  }
  if (!found) return false
  return !EVIDENCE_PATTERNS.some((p) => p.test(text))
}

// ── Coverage-claim enforcement (DC.5) ──────────────────────────────────────

export const COVERAGE_TARGET = 0.85

export const COMPLETION_FINALITY_PATTERNS = [
  /\b(?:final|complete|finished|done)\s+(?:e2e|coverage|test)\s+(?:push|expansion|wave)\b/i,
]

export function shouldBlockCoverageClaim(text: string): boolean {
  if (!text || text.trim().length === 0) return false
  let matched = false
  for (const pat of COMPLETION_FINALITY_PATTERNS) {
    if (pat.test(text)) { matched = true; break }
  }
  if (!matched) return false
  const covMatch = text.match(/(?:coverage|at)\s*(?:is\s*)?(\d+(?:\.\d+)?)\s*%/i)
  if (covMatch) {
    const pct = parseFloat(covMatch[1]) / 100
    if (pct >= COVERAGE_TARGET) return false
  }
  return true
}
