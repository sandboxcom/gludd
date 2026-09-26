/** Shared, validated configuration for the multitask enforcement plugin.
 *
 * This module deliberately lives outside `.opencode/plugin/`: OpenCode's
 * legacy plugin discovery treats every runtime export in that directory as a
 * plugin factory.  Keeping test-visible constants here preserves a one-source
 * contract without adding crash-prone named exports to the plugin module.
 */

function integerFromEnv(names: readonly string[], fallback: number): number {
  const raw = names.map(name => process.env[name]).find(value => value !== undefined)
  if (raw === undefined) return fallback
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

export const HARD_MAX_DISPATCHES = 10
export const MIN_DISPATCHES = integerFromEnv(
  ["GLUDD_MIN_DISPATCHES", "GLUDD_MULTITASK_MIN_DISPATCHES"],
  10,
)
export const MAX_DISPATCHES = Math.max(
  1,
  Math.min(
    HARD_MAX_DISPATCHES,
    integerFromEnv(["GLUDD_MULTITASK_MAX_DISPATCHES"], HARD_MAX_DISPATCHES),
  ),
)
export const MAX_ZERO_STREAK = 2
export const MSG_GAP_MS = integerFromEnv(["GLUDD_MSG_GAP_MS"], 5000)
export const CONSECUTIVE_NON_DISPATCH_THRESHOLD = integerFromEnv(
  ["GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD"],
  5,
)
export const CONSECUTIVE_NON_DISPATCH_WINDOW_MS = integerFromEnv(
  ["GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS"],
  30000,
)
export const MULTITASK_STATE_FILE =
  process.env.GLUDD_MULTITASK_STATE_FILE || "/tmp/gludd-multitask-state.json"
