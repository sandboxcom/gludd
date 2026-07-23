// Companion exports for enforce-multitask.ts.
// opencode's getLegacyPlugins() rejects non-function exports, so named
// constants/functions live here for tests to import.
export const MIN_DISPATCHES = parseInt(
  process.env.GLUDD_MIN_DISPATCHES ||
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES ||
  "10",
  10,
)
export const MAX_DISPATCHES = parseInt(process.env.GLUDD_MULTITASK_MAX_DISPATCHES || "10", 10)
export const MAX_ZERO_STREAK = 2
export const WAVE_HISTORY_SIZE = 10
export const CONSECUTIVE_NON_DISPATCH_THRESHOLD = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD || "5", 10)
export const CONSECUTIVE_NON_DISPATCH_WINDOW_MS = parseInt(
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS || "30000", 10)
export const MULTITASK_STATE_FILE = process.env.GLUDD_MULTITASK_STATE_FILE || "/tmp/gludd-multitask-state.json"
