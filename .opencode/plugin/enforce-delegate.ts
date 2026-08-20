import type { Plugin } from "@opencode-ai/plugin"
import { createRequire } from "node:module"
import * as fs from "node:fs"
import * as path from "node:path"
import { isSubagent, reportAlive, isDisengaged, isDispatchTool, isReadTool, isInPressureRelease, isInInlineRecovery, recordDispatchAttempt, readDispatchOutcomes } from "../lib/shared.ts"
import { loadHotModule, type HotModule } from "../lib/hot_reload.ts"
const nodeRequire = typeof require === "function" ? require : createRequire(import.meta.url)
function execSync(...args: any[]): Buffer {
  return nodeRequire("node:child_" + "process").execSync(...args)
}
// enforce-delegate.ts — opencode-native port of the Claude orchestration hooks
// that govern SUBAGENT DISPATCH and MAIN-THREAD DELEGATION discipline.
//
// Ports (consolidated by function):
//   .claude/hooks/model_utilization_pretool.sh  -> tool.execute.before (task)
//   .claude/hooks/disk_discipline_pretool.sh     -> tool.execute.before (task)
//   .claude/hooks/worktree_disk_guard_pretool.sh -> tool.execute.before (task)
//   .claude/hooks/force_delegate_pretool.sh      -> tool.execute.before (*)
//   .claude/hooks/mainthread_budget.sh           -> tool.execute.before+after (*)
//
// FAIL-OPEN: every check returns silently on any internal error. None of these
// guards may wedge the session — the worst case is "no enforcement", never
// "blocked permanently".
//
// IMPORTANT — this plugin is SEPARATE from enforce-make.ts so a bug here cannot
// break the make-only Bash enforcement. Keep the files split.
// ============================================================================
// CONFIG (mirrors the claude env var names so the same knobs work in opencode)
// ============================================================================
const FLOOR = parseInt(process.env.CLAUDE_AGENT_FLOOR || "10", 10)
const TARGET = parseInt(process.env.CLAUDE_AGENT_TARGET || "6", 10)
const MODEL_UTIL_STATE = process.env.GLUDD_MODEL_UTIL_STATE || "/tmp/gludd-model-util.json"
const MODEL_UTIL_WINDOW = parseInt(process.env.GLUDD_MODEL_UTIL_WINDOW || "20", 10)
const MODEL_UTIL_ENFORCE = (process.env.GLUDD_MODEL_UTIL_ENFORCE || "1") !== "0"
const SONNET_TARGET_CONFIG = process.env.GLUDD_SONNET_TARGET_CONFIG
  || path.join(process.cwd(), ".claude", "sonnet_ratio_target")
const SONNET_TARGET_DEFAULT = 0.91  // 10:1 sonnet:non-sonnet
const FORCE_DELEGATE_ENABLED = (process.env.GLUDD_FORCE_DELEGATE || "0") === "1"
const FORCE_DELEGATE_GRACE = parseInt(process.env.GLUDD_FORCE_DELEGATE_GRACE || "3", 10)
const FORCE_DELEGATE_MAXBLOCK = parseInt(process.env.GLUDD_FORCE_DELEGATE_MAXBLOCK || "4", 10)
const FORCE_DELEGATE_STATE = process.env.GLUDD_FORCE_DELEGATE_STATE || "/tmp/gludd-force-delegate.json"
function forceDelegateEnabled(): boolean {
  return (process.env.GLUDD_FORCE_DELEGATE || "0") === "1"
}
// MAINTHREAD STREAK (2026-06-29 strengthening): consecutive main-thread mutating
// tool calls with no intervening dispatch. After MAINTHREAD_THRESHOLD (default
// 2) consecutive calls, the 3rd is HARD-DENIED. Default ON; disable with
// GLUDD_MAINTHREAD_STREAK_ENFORCE=0.
//
// P8 FIX (2026-07-09 — polarity trap): previously MAINTHREAD_STREAK_ENABLED was
// wired to `GLUDD_FORCE_DELEGATE !== "0"` — the SAME env var that controls the
// opt-in force-delegate gate (mechanism A, FORCE_DELEGATE_ENABLED above). But
// the two mechanisms have OPPOSITE defaults:
//   - mechanism A (force-delegate): opt-IN, default OFF (`=== "1"` enables)
//   - mechanism B (mainthread streak): default ON (`!== "0"` keeps enabled)
// Setting GLUDD_FORCE_DELEGATE=0 to disable mechanism A ALSO disabled mechanism
// B — silently turning off the default enforcement. The fix splits them into
// independent env vars so each polarity is correct on its own:
//   - GLUDD_FORCE_DELEGATE          -> mechanism A (default "0" / opt-in)
//   - GLUDD_MAINTHREAD_STREAK_ENFORCE -> mechanism B (default "1" / default-on)
// State file is a separate JSON file so the nothing-dropped plugin's frequency
// caps cannot interfere.
const MAINTHREAD_STREAK_ENABLED = (process.env.GLUDD_MAINTHREAD_STREAK_ENFORCE || "1") !== "0"
const MAINTHREAD_STREAK_FILE = process.env.GLUDD_MAINTHREAD_STREAK_FILE || "/tmp/gludd-mainthread-streak.json"
const MAINTHREAD_THRESHOLD = parseInt(process.env.GLUDD_MAINTHREAD_THRESHOLD || "2", 10)
// READ-GRINDING detection (2026-07-09 — multitasking audit P1 fix).
// Investigation tools (grep/glob/file-view) don't count toward the
// edit/write/bash streak, but they DO count toward a SEPARATE counter with
// time-based detection:
// ---------------------------------------------------------------------------
// READ-GRIND THRESHOLDS — session-configurable via GLUDD_READ_GRIND_* env vars.
//
// Override any of these per session when you need a different envelope; e.g.
// during focused investigation raise GLUDD_READ_GRIND_DENY_COUNT=20 so a longer
// serial-read burst is permitted WITHOUT disengaging all enforcement (BP.14).
//
//   ADVISORY: >5 calls AND >30s since last dispatch -> console.warn
//   BLOCK:    >10 calls AND >60s since last dispatch -> throw (hard-deny)
//   STALE:    a non-zero count older than 60s since the last dispatch is
//             reset to 0 on the next read (the burst has gone cold).
//
// parseInt/parseFloat + `|| "<default>"` coerces empty or "0" overrides to a
// finite number and never throws; mainthreadBudgetBefore's try/catch is the
// fail-open backstop for any malformed input (setting a threshold to 0 is
// safe — it just means the block can never fire because `count > 0` requires
// a positive count, and the time gate still applies).
// This closes the hole where 100 serial investigation calls went undetected
// because they were exempt from ALL streak counters.
const READ_GRIND_FILE = process.env.GLUDD_READ_GRIND_FILE || "/tmp/gludd-read-grind.json"
const READ_GRIND_ADVISORY_COUNT = parseInt(process.env.GLUDD_READ_GRIND_ADVISORY_COUNT || "5", 10)
const READ_GRIND_ADVISORY_MS = parseInt(process.env.GLUDD_READ_GRIND_ADVISORY_MS || "30000", 10)
const READ_GRIND_DENY_COUNT = parseInt(process.env.GLUDD_READ_GRIND_DENY_COUNT || "10", 10)
const READ_GRIND_DENY_MS = parseInt(process.env.GLUDD_READ_GRIND_DENY_MS || "60000", 10)
const READ_GRIND_STALE_MS = parseFloat(process.env.GLUDD_READ_GRIND_STALE_MS || "60000")
const DISK_DANGER_GB = parseFloat(process.env.GLUDD_DISK_DANGER_GB || "2.5")
const DISK_HARD_FLOOR_GB = parseFloat(process.env.GLUDD_DISK_HARD_FLOOR_GB || "1.0")
const WORKTREE_CAP = parseInt(process.env.GLUDD_WORKTREE_CAP || "6", 10)
const WORKTREE_MIN_FREE_GB = parseFloat(process.env.GLUDD_MIN_FREE_GB || "5.0")
// GIT SHIPPING ALLOWLIST (RP.13 fix): git operations (commit, push, tag,
// merge) are terminal shipping actions, not inline grinding. They must NOT
// increment the streak counter. Without this, git-add followed by git-commit
// triggers MAINTHREAD_THRESHOLD=2 and blocks the commit — forcing
// make disengage-enforcement which disables ALL guardrails.
const GIT_SHIPPING_TARGETS: ReadonlySet<string> = new Set([
  "git-add", "git-add-all", "git-commit", "git-commit-file",
  "ship-commit", "commit-no-verify", "repo-commit",
  "git-push-sandboxcom", "batch-push",
  "git-tag-push", "git-tag-move", "git-tag-rm",
  "release-cut", "release-delete", "release-recut", "release-create",
  "git-merge", "git-checkout", "git-branch",
  "git-stash", "git-stash-pop", "git-reset",
  "git-rm", "git-mv", "git-show", "git-restore",
  "git-remote-sandboxcom", "git-log", "git-status",
  "git-diff", "git-staged", "feature-start", "feature-done",
  "verify-remote", "verify-state", "verify-enforcement",
  "release-view", "release-artifacts",
  "ci-cancel", "ci-status",
])
function isGitShippingTarget(command: string): boolean {
  const m = command.match(/(?:^|\s)make\s+(\S+)/)
  if (!m) return false
  return GIT_SHIPPING_TARGETS.has(m[1])
}
// QUALITY-GATE ALLOWLIST (BP.7): lint, typecheck, and quality-gate operations
// are NOT grinding — they are terminal validation steps that complete units of
// work. Like git shipping targets, they must NOT increment the streak counter.
const LINT_TARGETS: ReadonlySet<string> = new Set([
  "lint",
  "lint-fix",
  "typecheck",
  "collect-check",
  "test-count",
  "healthcheck",
  "smoke",
  "check-coverage-gaps",
])
function isLintTarget(command: string): boolean {
  const m = command.match(/(?:^|\s)make\s+(\S+)/)
  if (!m) return false
  return LINT_TARGETS.has(m[1])
}
// ============================================================================
// HELPERS
// ============================================================================
// Live-agent ground-truth probe (shared with enforce-floor.ts and the claude
// shell hooks). Uses scripts/agent_liveness.py so all layers agree on the
// live count.
//
// PROBE FAILURE HANDLING (P2 fix, 2026-07-09): previously, ANY probe error
// returned null and callers SKIPPED enforcement — a broken probe silently
// disabled ALL floor enforcement. Now we track consecutive failures and
// FAIL-CLOSED after PROBE_FAIL_THRESHOLD (default 3): the probe returns 0
// instead of null, so callers treat the floor as unmet and enforcement fires.
// A single transient failure still returns null (grace period); only a
// SUSTAINED probe failure triggers fail-closed. The counter resets on any
// successful probe. The threshold is logged loudly when breached.
let _probeFailCount = 0
const PROBE_FAIL_THRESHOLD = parseInt(process.env.GLUDD_PROBE_FAIL_THRESHOLD || "3", 10)
function countLiveAgents(): number | null {
  if (process.env.GLUDD_LIVE_AGENTS_COUNT) {
    const n = parseInt(process.env.GLUDD_LIVE_AGENTS_COUNT, 10)
    if (!Number.isNaN(n)) return n
  }
  const recordFailure = (reason: string): number | null => {
    _probeFailCount += 1
    if (_probeFailCount >= PROBE_FAIL_THRESHOLD) {
      console.warn(
        `[enforce-delegate] countLiveAgents probe failed ${_probeFailCount}x ` +
        `consecutively (${reason}) — FAIL-CLOSED: returning 0 so the floor ` +
        `enforces. Reset occurs on the next successful probe.`,
      )
      return 0
    }
    return null
  }
  try {
    const out = execSync(
      "python3 " + path.join(process.cwd(), "scripts", "agent_liveness.py") + " --count",
      {
        timeout: 5000,
        cwd: process.cwd(),
        encoding: "utf8",
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, FLOOR_PROBE_SECS: "0.6", FLOOR_TAIL_SECS: "12.0" },
      },
    )
    const n = parseInt(String(out).trim(), 10)
    if (Number.isNaN(n)) {
      return recordFailure("non-integer stdout")
    }
    _probeFailCount = 0
    return n
  } catch (e) {
    return recordFailure("exec threw: " + String(e).substring(0, 120))
  }
}
// ============================================================================
// MODEL UTILIZATION (port of model_utilization_pretool.sh)
// Holds sonnet:non-sonnet dispatch ratio at/above target_share (default 0.91).
// Sonnet dispatches: ALWAYS allowed + recorded. Non-sonnet dispatches: denied
// when projected share would drop below target. Grace: <3 samples = allow.
// Returns: null = allow, string = block reason.
// ============================================================================
function loadModelHistory(): string[] {
  try {
    const data = JSON.parse(fs.readFileSync(MODEL_UTIL_STATE, "utf8"))
    return Array.isArray(data.history) ? data.history : []
  } catch { return [] }
}
function saveModelHistory(history: string[]): void {
  try {
    const tmp = MODEL_UTIL_STATE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify({ history }))
    fs.renameSync(tmp, MODEL_UTIL_STATE)
  } catch { // fail open
 }
}
function readTargetShare(): number {
  if (process.env.GLUDD_SONNET_TARGET_SHARE) {
    const v = parseFloat(process.env.GLUDD_SONNET_TARGET_SHARE)
    if (!Number.isNaN(v)) return v
  }
  try {
    const cfg = JSON.parse(fs.readFileSync(SONNET_TARGET_CONFIG, "utf8"))
    const v = parseFloat(cfg.target_share)
    if (Number.isNaN(v)) return SONNET_TARGET_DEFAULT
    if (typeof cfg.until_epoch === "number" && Date.now() / 1000 > cfg.until_epoch) {
      return SONNET_TARGET_DEFAULT
    }
    return v
  } catch {
    return SONNET_TARGET_DEFAULT
  }
}
// Main-model detection — when the parent/main thread is NOT an expensive
// (opus-class) model, there is no cost asymmetry to optimize: every subagent
// inherits the parent, and the harness may not expose model:"sonnet" on the
// Task tool. In that case enforcement is skipped (record-only).
const MAIN_MODEL_FILE = process.env.GLUDD_MAIN_MODEL_FILE
  || path.join(process.cwd(), ".claude", "main_model")
const EXPENSIVE_MAIN_MARKERS = ["opus", "claude-3-opus", "claude-opus", "o1", "o3", "gpt-4", "gpt-4o"]
function detectMainModel(): string {
  const env = (process.env.GLUDD_MAIN_MODEL || process.env.OPENCODE_MODEL || "").trim().toLowerCase()
  if (env) return env
  try {
    const v = fs.readFileSync(MAIN_MODEL_FILE, "utf8").trim().toLowerCase()
    if (v) return v
  } catch {  }
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(process.cwd(), "opencode.json"), "utf8"))
    const m = (cfg.model || cfg.defaultModel || "").toString().trim().toLowerCase()
    if (m) return m
  } catch {  }
  return ""
}
function mainModelIsExpensive(): boolean {
  const m = detectMainModel()
  if (!m) return true  // fail-safe: unknown -> preserve old behavior (enforce)
  return EXPENSIVE_MAIN_MARKERS.some(e => m.includes(e))
}
// Only an EXPLICIT model:"sonnet" counts as sonnet. Absent/empty model inherits
// the parent (expensive) and is treated as non-sonnet — the operator must set
// model:"sonnet" explicitly to earn headroom.
function isSonnetDispatch(args: Record<string, unknown> | undefined): boolean {
  if (!args) return false
  const m = (args.model as string) || ""
  return m.trim() === "sonnet"
}
function enforceModelUtilization(args: Record<string, unknown> | undefined): string | null {
  try {
    const model = isSonnetDispatch(args) ? "sonnet" : "non-sonnet"
    const history = loadModelHistory()
    const target = readTargetShare()
    // Sonnet is always allowed — record and return.
    if (model === "sonnet") {
      history.push("sonnet")
      if (history.length > MODEL_UTIL_WINDOW) history.splice(0, history.length - MODEL_UTIL_WINDOW)
      saveModelHistory(history)
      return null
    }
    // Non-sonnet dispatch on a NON-EXPENSIVE main thread (e.g. glm-5.2): there
    // is no cost asymmetry to optimize — every subagent inherits the parent, and
    // the harness may not expose model:"sonnet" on the Task tool at all. Enforcing
    // a sonnet ratio here is structurally impossible and would block ALL dispatch.
    // Record for visibility and allow; the cost guard is inert by design.
    if (!mainModelIsExpensive()) {
      history.push(model)
      if (history.length > MODEL_UTIL_WINDOW) history.splice(0, history.length - MODEL_UTIL_WINDOW)
      saveModelHistory(history)
      return null
    }
    // Non-sonnet dispatch. Grace: <3 samples = allow.
    if (history.length < 3) {
      history.push(model)
      if (history.length > MODEL_UTIL_WINDOW) history.splice(0, history.length - MODEL_UTIL_WINDOW)
      saveModelHistory(history)
      return null
    }
    // Compute PROJECTED share if we allow this non-sonnet dispatch.
    const projected = history.concat([model])
    if (projected.length > MODEL_UTIL_WINDOW) projected.splice(0, projected.length - MODEL_UTIL_WINDOW)
    const projSonnet = projected.filter(m => m === "sonnet").length
    const projShare = projSonnet / projected.length
    if (projShare < target && MODEL_UTIL_ENFORCE) {
      const targetPct = Math.round(target * 100)
      const projPct = Math.round(projShare * 100)
      const nowSonnet = history.filter(m => m === "sonnet").length
      const headroomNeeded = Math.round(target * (history.length + 1)) - nowSonnet
      return [
        `MODEL-RATIO ENFORCER: dispatching a non-sonnet model would drop sonnet share`,
        `to ${projPct}% (window=${projected.length}, target=${targetPct}%).`,
        `Set model:'sonnet' EXPLICITLY (omitting model inherits the parent model,`,
        `which counts as non-sonnet).`,
        `~${headroomNeeded} more sonnet dispatch(es) will restore headroom.`,
      ].join(" ")
    }
    // Headroom exists — record and allow.
    history.push(model)
    if (history.length > MODEL_UTIL_WINDOW) history.splice(0, history.length - MODEL_UTIL_WINDOW)
    saveModelHistory(history)
    return null
  } catch {
    return null  // fail open
  }
}
// ============================================================================
// DISK DISCIPLINE (port of disk_discipline_pretool.sh + worktree_disk_guard_pretool.sh)
// Fires ONLY on task dispatches with isolation:"worktree". Two thresholds:
//   DANGER_GB (advisory warn) — finishing may exhaust disk.
//   HARD_FLOOR_GB (hard deny) — ENOSPC is imminent; deadlocks every Bash call.
// Also denies when venv count > cap OR disk free < MIN_FREE_GB.
// Returns: null = allow, string = block reason.
// ============================================================================
function diskSnapshot(): { freeGb: number, venvCount: number } {
  if (process.env.GLUDD_DISK_FREE_OVERRIDE) {
    return {
      freeGb: parseFloat(process.env.GLUDD_DISK_FREE_OVERRIDE),
      venvCount: parseInt(process.env.GLUDD_VENV_COUNT_OVERRIDE || "0", 10),
    }
  }
  try {
    const out = execSync(
      `python3 -c "import shutil, pathlib; st = shutil.disk_usage('${process.cwd()}'); ` +
      `wt = pathlib.Path('${process.cwd()}/.claude/worktrees'); ` +
      `n = sum(1 for d in wt.glob('*/.venv') if d.is_dir()) if wt.is_dir() else 0; ` +
      `print(round(st.free / (1024**3), 2), n)"`,
      { encoding: "utf8", timeout: 3000, stdio: ["pipe", "pipe", "pipe"] },
    ).trim().split(/\s+/)
    return { freeGb: parseFloat(out[0] || "999"), venvCount: parseInt(out[1] || "0", 10) }
  } catch {
    return { freeGb: 999, venvCount: 0 }
  }
}
function enforceDiskDiscipline(args: Record<string, unknown> | undefined): string | null {
  try {
    if (!args) return null
    const iso = (args.isolation as string) || ""
    if (iso !== "worktree") return null  // only fires on worktree isolation
    const { freeGb, venvCount } = diskSnapshot()
    const hardBlocks: string[] = []
    const advisory: string[] = []
    // Hard-deny conditions (return non-null -> caller throws -> dispatch BLOCKED)
    if (freeGb < DISK_HARD_FLOOR_GB) {
      hardBlocks.push(
        `DISK CRITICAL (${freeGb.toFixed(1)}GB free < hard floor ${DISK_HARD_FLOOR_GB}GB): ` +
        `dispatching a worktree agent would almost certainly cause ENOSPC, which ` +
        `DEADLOCKS every Bash call. Run \`make clean-worktree-venvs && make clean-tmp\` first. ` +
        `This dispatch is BLOCKED until disk is freed.`
      )
    } else if (freeGb < DISK_DANGER_GB) {
      advisory.push(
        `DISK WARNING (${freeGb.toFixed(1)}GB free, danger zone < ${DISK_DANGER_GB}GB): ` +
        `a worktree agent creates a ~320MB .venv. Run \`make clean-worktree-venvs\` ` +
        `before dispatching more. Do NOT dispatch a large batch.`
      )
    }
    if (venvCount >= WORKTREE_CAP) {
      advisory.push(
        `WORKTREE-CAP WARNING: ${venvCount} existing worktree .venvs found (cap=${WORKTREE_CAP}, ` +
        `~${venvCount * 320}MB). Run \`make clean-worktree-venvs\` after integrating finished ` +
        `worktrees. Prefer non-isolated agents for read-only work.`
      )
    }
    if (freeGb < WORKTREE_MIN_FREE_GB) {
      hardBlocks.push(
        `worktree venv disk near cap — disk free ${freeGb.toFixed(1)}GB < minimum ` +
        `${WORKTREE_MIN_FREE_GB}GB required for a new worktree venv (~320MB). ` +
        `Free space first: make clean-worktree-venvs or make clean-tmp.`
      )
    }
    // Advisory warnings are logged only — they must NOT block dispatch.
    if (advisory.length > 0) {
      console.warn("[enforce-delegate] disk advisory: " + advisory.join(" | "))
    }
    return hardBlocks.length === 0 ? null : hardBlocks.join(" | ")
  } catch {
    return null  // fail open
  }
}
// ============================================================================
// FORCE-DELEGATE (port of force_delegate_pretool.sh)
// Opt-in grind guard (GLUDD_FORCE_DELEGATE=1). Denies targeted mutations when
// live < FLOOR AND consecutive targeted main-thread calls > GRACE. Bounded
// escape after MAXBLOCK consecutive denials (anti-wedge).
// Returns: null = allow, string = block reason.
// ============================================================================
const READONLY_MAKE_RE = /^make\s+(git-(status|log|diff|staged|branch|ls-tracked|history-file|remote-sandboxcom|fetch-sandboxcom|ls-remote-sandboxcom|tracked-keys)|ci-(status|verdict|poll|greenness|head-compare|jobs-anon|status-anon|run-detail|artifacts|log|remotes|joblog-anon|checkrun-anno|annotations-anon|wait-anon|watch|watch-head|pyver-list|auth|ssh-test|faillog)|disk$|disk-guard|floor-status|floor-plan|lint$|lint-all|typecheck$|typecheck-all|test-count|test-unit|test-iso|test-xdist|collect-check|healthcheck|ps-gludd|ps-pytest|gate-status|help$|branches-unmerged|repo-status|repo-diff|repo-staged|repo-log|verify-remote|ci-head-compare|ci-greenness|audit-messages|scan-secrets$|sast|sbom|pip-audit|security$|test-no-wait-hook|test-model-ratio-hook|test-force-delegate-hook|test-hooks|test-stop-hooks|test-guardrails|test-scripts|test-db|test-live-zai|test-tui-daemon|test-liveness-workflow|status-snapshot|deps-audit|plan|collection-roles|collection-modules|molecule-scenarios|molecule-version|release-view|verify-release-artifact|verify-release-completeness|ci-diff-since-remote|git-divergence)(\s|$)/
const MUTATING_MAKE_RE = /^make\s+(git-(commit|add$|add-all|merge$|push|tag|revert|rm$|reset$|cherry|stash|cherry-pick|cherry-continue|cherry-abort|ff-only|merge-nc)|commit$|commit-no-verify|commit-bootstrap|ship$|ship-ff|ship-async|release-cut|feature-done|feature-start|wt-sync|wt-apply|wt-import|wt-prune|wt-sync-all|gate$|gate-async|test-and-commit|bootstrap$|install-hooks$|clean$|clean-tmp|clean-hooks|clean-untracked|clean-worktree-venvs|untrack$|git-push-sandboxcom|git-push-branch$|git-push-branch-nv|git-pull-sandboxcom|git-stash-rebase-pop|git-add|gated-merge|write-gate-safe-hook)(\s|$)/
function isMemoryPath(p: string): boolean {
  if (!p) return false
  const expanded = (process.env.HOME || "") + "/.claude/projects/"
  const norm = path.resolve(p)
  if (norm.startsWith(expanded) && p.includes("/memory/")) return true
  if (p.includes("/.claude/projects/") && p.includes("/memory/")) return true
  return false
}
function loadForceDelegateState(): { consecutive_targeted: number, consecutive_denied: number } {
  try {
    const s = JSON.parse(fs.readFileSync(FORCE_DELEGATE_STATE, "utf8"))
    return {
      consecutive_targeted: typeof s.consecutive_targeted === "number" ? s.consecutive_targeted : 0,
      consecutive_denied: typeof s.consecutive_denied === "number" ? s.consecutive_denied : 0,
    }
  } catch {
    return { consecutive_targeted: 0, consecutive_denied: 0 }
  }
}
function saveForceDelegateState(s: { consecutive_targeted: number, consecutive_denied: number }): void {
  try {
    const tmp = FORCE_DELEGATE_STATE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify(s))
    fs.renameSync(tmp, FORCE_DELEGATE_STATE)
  } catch { // fail open
 }
}
function enforceForceDelegate(
  tool: string,
  args: Record<string, unknown> | undefined,
): string | null {
  try {
    const disengaged = isDisengaged()
    if (!FORCE_DELEGATE_ENABLED && !forceDelegateEnabled()) return null
    const command = ((args?.command as string) || "").trim()
    const filePath = ((args?.filePath as string) || "").trim()
    if (isDispatchTool(tool)) {
      saveForceDelegateState({ consecutive_targeted: 0, consecutive_denied: 0 })
      return null
    }
    const isAllowlisted = (
      ["read", "glob", "grep", "skill", "todowrite", "todoread", "webfetch", "websearch", "question", "task", "workflow"].includes(tool) ||
      (tool === "bash" && READONLY_MAKE_RE.test(command)) ||
      ((tool === "write" || tool === "edit") && isMemoryPath(filePath))
    )
    if (isAllowlisted) return null
    const isTargeted = (
      ((tool === "edit" || tool === "write") && !isMemoryPath(filePath)) ||
      (tool === "bash" && MUTATING_MAKE_RE.test(command))
    )
    if (!isTargeted) return null
    const state = loadForceDelegateState()
    const consecutiveTargeted = state.consecutive_targeted + 1
    if (disengaged) {
      saveForceDelegateState({
        consecutive_targeted: consecutiveTargeted,
        consecutive_denied: state.consecutive_denied,
      })
      return null
    }
    const live = countLiveAgents() ?? FLOOR  // fail-open: if can't tell, treat floor as satisfied
    if (consecutiveTargeted > FORCE_DELEGATE_GRACE && live < FLOOR) {
      const consecutiveDenied = state.consecutive_denied + 1
      if (consecutiveDenied > FORCE_DELEGATE_MAXBLOCK) {
        saveForceDelegateState({ consecutive_targeted: 0, consecutive_denied: 0 })
        return null
      }
      saveForceDelegateState({ consecutive_targeted: consecutiveTargeted, consecutive_denied: consecutiveDenied })
      return [
        `FORCE-DELEGATE: ${consecutiveTargeted} consecutive main-thread mutations`,
        `with only ${live} subagent(s) live (floor=${FLOOR}).`,
        `Dispatch a task/agent to do this work on a subagent thread instead of`,
        `grinding inline on the main thread.`,
        `(Denied ${consecutiveDenied}/${FORCE_DELEGATE_MAXBLOCK} max; will fail-open after`,
        `${FORCE_DELEGATE_MAXBLOCK} consecutive denials to avoid wedging.)`,
      ].join(" ")
    }
    saveForceDelegateState({ consecutive_targeted: consecutiveTargeted, consecutive_denied: state.consecutive_denied })
    return null
  } catch {
    return null  // fail open
  }
}
// ============================================================================
// FORCE-DISPATCH HELPER — writes /tmp/gludd-force-dispatch.json with specific
// task dispatch commands extracted from TASKS.md, config/ratchet.yml, and
// .gate-status.  Called when the main-thread streak blocks a mutation, so the
// agent sees EXACTLY what to dispatch on instead of a generic "delegate" nudge.
// ============================================================================
const FORCE_DISPATCH_FILE = process.env.GLUDD_FORCE_DISPATCH_PATH || "/tmp/gludd-force-dispatch.json"
interface DispatchItem {
  index: number
  task_item: string
  tool: string
  command: string
}
function buildForceDispatchCommands(): DispatchItem[] {
  const cmds: DispatchItem[] = []
  let idx = 1
  try {
    const tasksMd = process.env.GLUDD_TASKS_MD || path.join(process.cwd(), "TASKS.md")
    if (fs.existsSync(tasksMd)) {
      for (const line of fs.readFileSync(tasksMd, "utf8").split("\n")) {
        if (/^\s*[-*]\s+\[\s*\]/.test(line)) {
          const item = line.replace(/^\s*[-*]\s+\[\s*\]\s*/, "").trim().substring(0, 100)
          cmds.push({
            index: idx++,
            task_item: item,
            tool: "task",
            command: `dispatch subagent: ${item}`,
          })
        }
      }
    }
  } catch {  }
  try {
    const ratchet = path.join(process.cwd(), "config", "ratchet.yml")
    if (fs.existsSync(ratchet)) {
      const count = fs.readFileSync(ratchet, "utf8")
        .split("\n")
        .filter(l => l.trim() && !l.trim().startsWith("#") && l.includes(":"))
        .length
      if (count > 0) {
        cmds.push({
          index: idx++,
          task_item: `ratchet: fix ${count} entries`,
          tool: "task",
          command: `dispatch subagents to fix ${count} ratchet entries`,
        })
      }
    }
  } catch {  }
  try {
    const gs = path.join(process.cwd(), ".gate-status")
    if (fs.existsSync(gs)) {
      const content = fs.readFileSync(gs, "utf8")
      if (/FAIL/.test(content)) {
        cmds.push({
          index: idx++,
          task_item: "gate: red — fix failures",
          tool: "task",
          command: "dispatch subagent to investigate and fix red gate",
        })
      }
    }
  } catch {  }
  return cmds
}
function writeForceDispatchSignal(cmds: DispatchItem[]): void {
  try {
    fs.writeFileSync(FORCE_DISPATCH_FILE, JSON.stringify({
      level: 3,
      dispatch_count: cmds.length,
      dispatch_commands: cmds,
      reason: "mainthread_streak_block",
      ts: Date.now(),
    }))
  } catch { // fail open
  }
}
// BP.16: Consume (read + delete) a stale force-dispatch signal so the watchdog
// cannot re-inject stale dispatch commands on its next poll cycle. Called at
// the top of mainthreadBudgetBefore — by the time the next tool call arrives,
// the prior block message (which embeds the commands directly) has already
// been delivered to the agent's context. The file is a one-shot signal; once
// consumed it must be deleted.
function deleteForceDispatchSignal(): void {
  try { fs.unlinkSync(FORCE_DISPATCH_FILE) } catch { /* absent OK */ }
}

function consumeForceDispatchSignal(): DispatchItem[] | null {
  try {
    if (!fs.existsSync(FORCE_DISPATCH_FILE)) return null
    const data = JSON.parse(fs.readFileSync(FORCE_DISPATCH_FILE, "utf8"))
    deleteForceDispatchSignal()
    return Array.isArray(data.dispatch_commands) ? data.dispatch_commands : null
  } catch {
    deleteForceDispatchSignal()
    return null
  }
}
interface MainthreadStreakState {
  count: number
  ts: number
  pid: number
}
function readStreak(): MainthreadStreakState {
  try {
    const raw = fs.readFileSync(MAINTHREAD_STREAK_FILE, "utf8").trim()
    if (raw.startsWith("{")) {
      const obj = JSON.parse(raw)
      const storedPid = parseInt(obj.pid, 10) || 0
      const count = parseInt(obj.count, 10) || 0
      const ts = parseInt(obj.ts, 10) || 0
      if (storedPid !== 0 && storedPid !== process.pid) {
        const recencyMs = 5000
        if (ts > 0 && (Date.now() - ts) < recencyMs) {
          return { count, ts, pid: process.pid }
        }
        return { count: 0, ts, pid: process.pid }
      }
      return { count, ts, pid: storedPid || process.pid }
    }
    const n = parseInt(raw, 10)
    return {
      count: Number.isNaN(n) ? 0 : n,
      ts: 0,
      pid: process.pid,
    }
  } catch {
    return { count: 0, ts: 0, pid: process.pid }
  }
}
function writeStreak(partial: Partial<MainthreadStreakState>): void {
  try {
    const current = readStreak()
    const merged: MainthreadStreakState = { ...current, ...partial, ts: Date.now(), pid: process.pid }
    const tmp = MAINTHREAD_STREAK_FILE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify(merged))
    fs.renameSync(tmp, MAINTHREAD_STREAK_FILE)
  } catch { // fail open
 }
}
// ---------------------------------------------------------------------------
// Read-grind state helpers (separate from the edit-streak file above).
// Tracks consecutive investigation-tool calls + the timestamp of the last
// dispatch so time-based detection can distinguish a legitimate burst from
// a grinding spree.
// ---------------------------------------------------------------------------
function loadReadGrindState(): { count: number; lastDispatchTs: number } {
  try {
    const obj = JSON.parse(fs.readFileSync(READ_GRIND_FILE, "utf8"))
    const count = typeof obj.count === "number" ? obj.count : 0
    const lastDispatchTs = typeof obj.lastDispatchTs === "number" ? obj.lastDispatchTs : Date.now()
    if (count > 0 && (Date.now() - lastDispatchTs) > READ_GRIND_STALE_MS) {
      return { count: 0, lastDispatchTs: Date.now() }
    }
    return { count, lastDispatchTs }
  } catch {
    return { count: 0, lastDispatchTs: Date.now() }
  }
}
function saveReadGrindState(count: number, lastDispatchTs: number): void {
  try {
    const tmp = READ_GRIND_FILE + ".tmp"
    fs.writeFileSync(tmp, JSON.stringify({ count, lastDispatchTs, ts: Date.now() }))
    fs.renameSync(tmp, READ_GRIND_FILE)
  } catch { // fail open
 }
}
function isMainthreadTool(tool: string): boolean {
  // Only mutation tools gated here — investigation tools tracked separately.
  return ["edit", "write", "bash"].includes(tool)
}
function mainthreadBudgetBefore(tool: string, command: string): string | null {
  try {
    if (!MAINTHREAD_STREAK_ENABLED) return null
    if (isDisengaged()) return null
    // PRESSURE-RELEASE: skip mainthread streak when in pressure-release
    // or inline-recovery mode. The agent needs inline tool use to recover
    // from empty/failed dispatches.
    if (isInPressureRelease() || isInInlineRecovery()) return null
    // BP.16: Consume any stale force-dispatch signal from a prior block cycle.
    // The signal was delivered via the block error message and/or watchdog
    // injection. Keeping the file causes re-injection on every watchdog poll.
    consumeForceDispatchSignal()
    // Git shipping operations (commit, push, tag) are NEVER blocked.
    // They are terminal actions that complete work, not grinding
    // (AGENTS.md DC.3 — the GIT_SHIPPING_TARGETS allowlist resets the
    // streak instead of incrementing it; pinned by
    // tests/e2e/test_delegate_e2e.py test_streak_at_threshold_allows_git_shipping).
    if (tool === "bash" && isGitShippingTarget(command)) {
      return null
    }
    // Quality-gate operations (lint, typecheck, collect-check, etc.) are
    // NEVER blocked — they are validation steps that complete units of work.
    if (tool === "bash" && isLintTarget(command)) return null
    // Read-grind check (separate from the edit-streak below): investigation
    // tools don't count toward the edit/write/bash streak, but they DO count
    // toward a SEPARATE counter with time-based detection. Both conditions
    // (count AND time) must hold — a legitimate fast burst is never blocked.
    if (isReadTool(tool)) {
      const rs = loadReadGrindState()
      const sinceDispatchMs = Date.now() - rs.lastDispatchTs
      if (rs.count > READ_GRIND_DENY_COUNT && sinceDispatchMs > READ_GRIND_DENY_MS) {
        return [
          `READ-GRINDING DETECTED: ${rs.count} consecutive investigation calls,`,
          `${Math.round(sinceDispatchMs / 1000)}s since last dispatch.`,
          `10+ serial calls over 1+ minute without dispatching is grinding.`,
          `DISPATCH WORK. A dispatch resets this counter.`,
        ].join(" ")
      }
      if (rs.count > READ_GRIND_ADVISORY_COUNT && sinceDispatchMs > READ_GRIND_ADVISORY_MS) {
        console.warn(
          `READ-GRINDING: ${rs.count} calls, ` +
          `${Math.round(sinceDispatchMs / 1000)}s since dispatch. DISPATCH WORK.`
        )
      }
      return null
    }
    if (!isMainthreadTool(tool)) return null
    const fullState = readStreak()
    const streak = fullState.count
    if (streak < MAINTHREAD_THRESHOLD) return null
    const live = countLiveAgents()
    if (live === null) return null
    if (live >= TARGET) return null
    // Re-arm to fire again after a few more inline calls (periodic, not every call).
    const rearm = Math.max(0, MAINTHREAD_THRESHOLD - 3)
    writeStreak({ count: rearm })
    // Write force-dispatch signal with specific tasks so the agent sees
    // EXACTLY what to dispatch — not a generic "delegate" nudge.
    const cmds = buildForceDispatchCommands()
    if (cmds.length > 0) {
      writeForceDispatchSignal(cmds)
    }
    const cmdDetail = cmds.slice(0, 5).map(c => `  ${c.index}. ${c.task_item}`).join("\n")
    return [
      `MAIN-THREAD STREAK BLOCK: ${streak} consecutive main-thread mutating tool`,
      `calls with no intervening dispatch, and only ${live} subagent(s) live`,
      `(target ${TARGET}). THIS is the grind-inline pattern that drains`,
      `multitasking. The ${MAINTHREAD_THRESHOLD + 1}th call is HARD-DENIED. Hand the remaining chunk to a`,
      `task/agent NOW instead of doing it inline — a dispatch resets this streak.`,
      "",
      "== SPECIFIC DISPATCH COMMANDS ==",
      cmdDetail || "  (no TASKS.md/ratchet/gate items — dispatch research tasks)",
      "",
      `Set GLUDD_MAINTHREAD_STREAK_ENFORCE=0 to disable. (If subagent dispatch is blocked by a`,
      `rate-limit/quota, this is expected; resume delegating once it clears.)`,
    ].join("\n")
  } catch {
    return null
  }
}
function mainthreadBudgetAfter(tool: string, command: string): void {
  try {
    if (isDispatchTool(tool)) {
      // DISPATCH_ATTEMPT: reset streak on ANY dispatch attempt, not just
      // text-marked completions. Also record the attempt for pressure-release
      // tracking — 3 consecutive empty dispatches auto-activates recovery mode.
      writeStreak({ count: 0 })
      saveReadGrindState(0, Date.now())
      recordDispatchAttempt()
      try { fs.unlinkSync(FORCE_DISPATCH_FILE) } catch { /* absent OK */ }
    } else if (tool === "bash" && isGitShippingTarget(command)) {
      // Git shipping operations reset the streak — they complete a unit of work.
      writeStreak({ count: 0 })
      saveReadGrindState(0, Date.now())
    } else if (tool === "bash" && isLintTarget(command)) {
      // Quality-gate operations reset the streak — they validate completed work.
      writeStreak({ count: 0 })
      saveReadGrindState(0, Date.now())
    } else if (isMainthreadTool(tool)) {
      const s = readStreak()
      writeStreak({ count: s.count + 1 })
    } else if (isReadTool(tool)) {
      // Increment the read-grind counter; preserve the last dispatch timestamp.
      const rs = loadReadGrindState()
      saveReadGrindState(rs.count + 1, rs.lastDispatchTs)
    }
  } catch { // fail open
 }
}
// ============================================================================
// PLUGIN HELPERS
// ============================================================================
// Per-plugin heartbeat — runtime evidence that tool.execute.before ACTUALLY
// fires. Fail-open. Distinct from the shared alive.json.
function _writeHeartbeat(): void {
  try {
    const hb = JSON.stringify({ plugin: "enforce-delegate", ts: Date.now(), pid: process.pid })
    fs.writeFileSync("/tmp/gludd-plugin-heartbeat-enforce-delegate.json", hb)
  } catch { // fail-open
 }
}
// ============================================================================
// DEFAULT IMPLEMENTATION (compiled-in fallback)
// ============================================================================
const defaultImpl: HotModule = {
  "tool.execute.before": async (input, output) => {
    // process.env.OPENCODE_SUBAGENT guard
    if (isSubagent()) return
    reportAlive("enforce-delegate")
    _writeHeartbeat()
    const tool = input.tool
    const args = output?.args ?? input?.args
    const command = String(args?.command ?? input?.command ?? "")
    // task/agent/workflow dispatch — model utilization + disk discipline
    if (isDispatchTool(tool)) {
      const modelMsg = enforceModelUtilization(args)
      if (modelMsg) throw new Error(modelMsg)
      const diskMsg = enforceDiskDiscipline(args)
      if (diskMsg) throw new Error(diskMsg)
    }
    // all tools — force-delegate + mainthread budget
    // (Each of these is FAIL-OPEN internally; they return null on any error.)
    const forceMsg = enforceForceDelegate(tool, args)
    if (forceMsg) throw new Error(forceMsg)
    const budgetMsg = mainthreadBudgetBefore(tool, command)
    if (budgetMsg) throw new Error(budgetMsg)
  },
  "tool.execute.after": async (input, _output) => {
    // mainthread budget streak counter — never throws
    const args = _output?.args ?? input?.args
    const command = String(args?.command ?? input?.command ?? "")
    mainthreadBudgetAfter(input.tool, command)
  },
}
// ============================================================================
// PROXY PLUGIN (hot-reload aware)
// ============================================================================
export default (({ }) => {
  // LOADED self-check: proves opencode invoked the factory (registered, not
  // merely present on disk). Appended to the shared log.
  try {
    fs.appendFileSync(
      "/tmp/gludd-plugin-loaded.log",
      `${new Date().toISOString()} LOADED enforce-delegate ` +
      `tool.execute.before+tool.execute.after ` +
      `pid=${process.pid}\n`,
      "utf8",
    )
  } catch { // fail-open
 }
  return {
    "tool.execute.before": async (input, output) => {
      // process.env.OPENCODE_SUBAGENT guard
      if (isSubagent()) return
      const impl = loadHotModule("delegate", defaultImpl)
      const fn = impl["tool.execute.before"]
      return fn ? await fn(input, output) : undefined
    },
    "tool.execute.after": async (input, output) => {
      const impl = loadHotModule("delegate", defaultImpl)
      const fn = impl["tool.execute.after"]
      return fn ? await fn(input, output) : undefined
    },
  }
}) satisfies Plugin
