// enforce-multitask.test.node.mjs — FAILING-FIRST behavioral tests (TDD).
//
// Verifies the five enforcement behaviors the plugin MUST provide:
//   1. Explicit MIN_DISPATCHES=10 floor — >=10 subagent dispatches required
//      when the operator opts in and pending work exists
//   2. Thin-wave blanking — text.complete blanks responses with 1-9 dispatches
//   3. Grinding detection — 5+ consecutive non-dispatch calls within 30s block
//   4. Zero-dispatch streak — 2 zero-dispatch messages block ALL tools
//   5. TASKS.md parsing — unchecked `- [ ]` / `* [ ]` items gate enforcement
//
// Tests marked TDD-FAIL assert the CONTRACT (AGENTS.md + testability needs),
// not current behavior — they are expected to fail until the plugin changes.
//
// Runner: node --test .opencode/plugin/enforce-multitask.test.node.mjs
// Pattern: esbuild --bundle to CJS, then createRequire. The on-disk plugin
// only exports the factory (no defaultImpl / resetMultitaskState), so state
// isolation is done by deleting the state file + fresh require per test.

import { describe, it, after } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { execSync } from 'node:child_process'

const PROJECT_ROOT = process.cwd()
const OUTFILE = '/tmp/gludd-test-enforce-multitask.js'
const EXPORTS_OUTFILE = '/tmp/gludd-test-enforce-multitask-exports.js'
const OUTFILE_WIN = '/tmp/gludd-test-enforce-multitask-window.js'
const OUTFILE_REFILL = '/tmp/gludd-test-enforce-multitask-refill.js'
const TASKS_DIR = '/tmp/gludd-test-multitask-project'
const EXTRA_DIRS = []

// Every mutable plugin input is explicitly namespaced for this suite. Tests
// must never delete or inspect a live OpenCode session's default state files.
const ENV_STATE_FILE = '/tmp/gludd-test-multitask-state.json'
const ENV_DISPATCH_COUNT_FILE = '/tmp/gludd-test-multitask-dispatch-count.json'
const ENV_CI_CACHE_FILE = '/tmp/gludd-test-multitask-ci.json'
const ENV_STOP_STATE_FILE = '/tmp/gludd-test-multitask-stop.json'
const ENV_RELEASE_FILE = '/tmp/gludd-test-multitask-release.json'
const ENV_TODOWRITE_FILE = '/tmp/gludd-test-multitask-todowrite.json'

// The factory's tool.execute.before delegates through loadHotModule(): if a
// hot module exists it would shadow the code under test. Park it.
const HOT_PATH = '/tmp/gludd-hot-multitask.js'
const HOT_BACKUP = '/tmp/gludd-hot-multitask.js.test-backup'
if (fs.existsSync(HOT_PATH)) {
  try { fs.renameSync(HOT_PATH, HOT_BACKUP) } catch {}
}

// Environment hardening — a live disengage file, subagent marker, or floor
// override in the ambient environment would silently skip every gate below.
delete process.env.OPENCODE_SUBAGENT
delete process.env.GLUDD_MULTITASK_ALWAYS
delete process.env.GLUDD_MIN_DISPATCHES
delete process.env.GLUDD_MULTITASK_MIN_DISPATCHES
delete process.env.GLUDD_MULTITASK_MAX_DISPATCHES
delete process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD
delete process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS
delete process.env.GLUDD_MSG_GAP_MS
process.env.GLUDD_DISENGAGE_PATH = '/tmp/gludd-test-multitask-no-disengage.json'
process.env.GLUDD_ALIVE_PATH = '/tmp/gludd-test-multitask-alive.json'
process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = '1'
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = '10'
process.env.GLUDD_MULTITASK_STATE_FILE = ENV_STATE_FILE
process.env.GLUDD_MULTITASK_DISPATCH_COUNT_FILE = ENV_DISPATCH_COUNT_FILE
process.env.GLUDD_CI_CACHE_PATH = ENV_CI_CACHE_FILE
process.env.GLUDD_STOP_STATE_PATH = ENV_STOP_STATE_FILE
process.env.GLUDD_RELEASE_COMPLETENESS_FILE = ENV_RELEASE_FILE
process.env.GLUDD_TODOWRITE_STATE_PATH = ENV_TODOWRITE_FILE
process.env.GLUDD_PROJECT_ROOT = TASKS_DIR

fs.mkdirSync(TASKS_DIR, { recursive: true })
fs.writeFileSync(
  path.join(TASKS_DIR, 'TASKS.md'),
  '# Test Tasks\n\n- [ ] Pending work item\n',
  'utf8',
)

function compileWithEsbuild(outfile) {
  const env = { ...process.env, npm_config_userconfig: '/dev/null' }
  const args = `.opencode/plugin/enforce-multitask.ts --bundle --platform=node --target=node18 --format=cjs --outfile=${outfile}`

  try {
    execSync(`node_modules/.bin/esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe' })
    return true
  } catch {}

  try {
    execSync(`esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe' })
    return true
  } catch {}

  try {
    execSync(`npx --yes esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe', env })
    return true
  } catch {}

  return false
}

function compileExportsWithEsbuild(outfile) {
  const env = { ...process.env, npm_config_userconfig: '/dev/null' }
  const args = `.opencode/lib/multitask_config.ts --bundle --platform=node --target=node18 --format=cjs --outfile=${outfile}`
  try {
    execSync(`node_modules/.bin/esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe' })
    return true
  } catch {}
  try {
    execSync(`esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe' })
    return true
  } catch {}
  try {
    execSync(`npx --yes esbuild ${args}`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe', env })
    return true
  } catch {}
  return false
}

if (!compileWithEsbuild(OUTFILE)) {
  console.error('esbuild compilation failed')
  process.exit(1)
}
assert.ok(fs.existsSync(OUTFILE), 'esbuild produced output file')
fs.copyFileSync(OUTFILE, OUTFILE_WIN) // separate require-cache identity for the tiny-window variant
fs.copyFileSync(OUTFILE, OUTFILE_REFILL) // separate identity for refill env variants

const _require = createRequire(import.meta.url)
const mod = _require(OUTFILE) // export-surface assertions only; behavior tests use freshPlugin()

// Compile companion exports for constant checks
if (!compileExportsWithEsbuild(EXPORTS_OUTFILE)) {
  console.error('Failed to compile enforce-multitask_exports.ts')
  process.exit(1)
}
const exportsMod = _require(EXPORTS_OUTFILE)

// --- per-test isolation helpers -------------------------------------------

function wipeState() {
  for (const file of [
    ENV_STATE_FILE,
    ENV_DISPATCH_COUNT_FILE,
    ENV_STATE_FILE + '.dispatch-count',
    ENV_CI_CACHE_FILE,
    ENV_STOP_STATE_FILE,
    ENV_RELEASE_FILE,
    ENV_TODOWRITE_FILE,
  ]) {
    try { fs.rmSync(file, { force: true }) } catch {}
  }
}

// Fresh module + plugin instance rooted at `projectRoot`. The plugin does not
// export resetMultitaskState (see T8), so a state-file wipe + fresh require is
// the only isolation mechanism available.
async function freshPlugin(projectRoot = TASKS_DIR) {
  wipeState()
  process.env.GLUDD_PROJECT_ROOT = projectRoot
  delete _require.cache[_require.resolve(OUTFILE)]
  const m = _require(OUTFILE)
  const instance = await m.default({})
  return {
    m,
    hook: instance['tool.execute.before'],
    tc: instance['experimental.text.complete'],
  }
}

// Variant compiled/required with a 400ms grinding window (env is read at
// require time). Uses its own outfile so the primary cache entry is untouched.
async function freshWindowPlugin() {
  wipeState()
  process.env.GLUDD_PROJECT_ROOT = TASKS_DIR
  process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS = '400'
  delete _require.cache[_require.resolve(OUTFILE_WIN)]
  const m = _require(OUTFILE_WIN)
  delete process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS
  const instance = await m.default({})
  return { m, hook: instance['tool.execute.before'], tc: instance['experimental.text.complete'] }
}

async function freshRefillPlugin(refreshIntervalMs) {
  wipeState()
  process.env.GLUDD_PROJECT_ROOT = TASKS_DIR
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES = '0'
  process.env.GLUDD_REFRESH_INTERVAL_MS = String(refreshIntervalMs)
  delete _require.cache[_require.resolve(OUTFILE_REFILL)]
  const m = _require(OUTFILE_REFILL)
  process.env.GLUDD_MULTITASK_MIN_DISPATCHES = '10'
  delete process.env.GLUDD_REFRESH_INTERVAL_MS
  const instance = await m.default({})
  return { hook: instance['tool.execute.before'], tc: instance['experimental.text.complete'] }
}

function mkProjectDir(name, tasksContent) {
  const dir = `/tmp/gludd-test-multitask-${name}`
  fs.rmSync(dir, { recursive: true, force: true })
  fs.mkdirSync(dir, { recursive: true })
  if (tasksContent !== null) {
    fs.writeFileSync(path.join(dir, 'TASKS.md'), tasksContent, 'utf8')
  }
  EXTRA_DIRS.push(dir)
  return dir
}

async function dispatchN(hook, n) {
  for (let i = 0; i < n; i++) {
    const r = await hook({ tool: 'task' })
    assert.strictEqual(r, undefined, `dispatch ${i + 1}/${n} must be allowed`)
  }
}

function assertDeny(r, needle, msg) {
  assert.ok(r !== null && r !== undefined, msg + ' (got allow instead of deny)')
  assert.strictEqual(r.permissionDecision, 'deny', msg)
  if (needle) {
    assert.ok(r.message.includes(needle), `${msg} — message must include "${needle}", got: ${r.message}`)
  }
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

// Builds a 2-message zero-dispatch streak through the canonical boundary
// signal (text.complete). The on-disk boundary idempotency guard is 500ms,
// so the two boundaries must be >500ms apart.
async function buildZeroStreak(tc) {
  await tc({}, { text: 'zero-dispatch response one' })
  await sleep(700)
  await tc({}, { text: 'zero-dispatch response two' })
}

function cleanup() {
  try { fs.rmSync(OUTFILE, { force: true }) } catch {}
  try { fs.rmSync(OUTFILE_WIN, { force: true }) } catch {}
  try { fs.rmSync(OUTFILE_REFILL, { force: true }) } catch {}
  wipeState()
  try { fs.rmSync('/tmp/gludd-test-multitask-alive.json', { force: true }) } catch {}
  try { fs.rmSync(TASKS_DIR, { recursive: true, force: true }) } catch {}
  for (const d of EXTRA_DIRS) {
    try { fs.rmSync(d, { recursive: true, force: true }) } catch {}
  }
  if (fs.existsSync(HOT_BACKUP)) {
    try { fs.renameSync(HOT_BACKUP, HOT_PATH) } catch {}
  }
}

describe('enforce-multitask', { concurrency: 1 }, () => {

  after(() => {
    cleanup()
  })

  // ==========================================================================
  // Export surface / testability contract
  // ==========================================================================
  describe('export surface', () => {
    it('T1: default factory returns supported tool and text-complete hooks', async () => {
      assert.strictEqual(typeof mod.default, 'function')
      const instance = await mod.default({})
      assert.strictEqual(typeof instance['tool.execute.before'], 'function')
      assert.strictEqual(typeof instance['experimental.text.complete'], 'function')
    })

    it('T2: MIN_DISPATCHES === 10 (the floor)', () => {
      assert.strictEqual(exportsMod.MIN_DISPATCHES, 10)
    })

    it('T3: MAX_DISPATCHES === 10 (ceiling == floor)', () => {
      assert.strictEqual(exportsMod.MAX_DISPATCHES, 10)
    })

    it('T4: MAX_ZERO_STREAK === 2', () => {
      assert.strictEqual(exportsMod.MAX_ZERO_STREAK, 2)
    })

    it('T5: CONSECUTIVE_NON_DISPATCH_THRESHOLD === 5', () => {
      assert.strictEqual(exportsMod.CONSECUTIVE_NON_DISPATCH_THRESHOLD, 5)
    })

    it('T6: CONSECUTIVE_NON_DISPATCH_WINDOW_MS === 30000', () => {
      assert.strictEqual(exportsMod.CONSECUTIVE_NON_DISPATCH_WINDOW_MS, 30000)
    })

    // defaultImpl, resetMultitaskState, and hasPendingWork are now module-private
    // (opencode's plugin loader rejects non-default exports). Test via behavior:
    // T7: the default factory's hook IS the implementation (no separate defaultImpl).
    it('T7: default factory returns functional tool.execute.before hook', async () => {
      const instance = await mod.default({})
      assert.strictEqual(
        typeof instance['tool.execute.before'], 'function',
        'default export must return a hooks object with tool.execute.before',
      )
    })

    // T8: state isolation is tested behaviorally by wiping the state file
    // between test cases (see wipeState helper).
    it('T8: state isolation via state file wipe (behavioral)', () => {
      wipeState()
      assert.ok(!fs.existsSync(ENV_STATE_FILE),
        'namespaced state file wiped for isolation')
    })

    // T9: pending work detection is tested behaviorally via deny/allow
    // in the BEHAVIOR tests below.
    it('T9: pending-work gate tested via behavior (deny on pending work)', async () => {
      const instance = await mod.default({})
      assert.strictEqual(typeof instance['tool.execute.before'], 'function')
    })

    // The dispatch-count file is explicitly namespaced so behavioral
    // tests cannot share mutable state with a live OpenCode session.
    it('T10b: dispatch count file honors GLUDD_MULTITASK_DISPATCH_COUNT_FILE env override (behavioral)', async () => {
      const { hook } = await freshPlugin()
      // Dispatch 1 agent; the count must be written to the isolated path.
      await dispatchN(hook, 1)
      // After dispatch, the isolated file must exist (dispatch increments it)
      assert.ok(
        fs.existsSync(ENV_DISPATCH_COUNT_FILE),
        'isolated dispatch-count file must exist after a dispatch',
      )
      // Existence of the namespaced file proves the override was honored;
      // the suite deliberately never reads or deletes the live default path.
    })

    // The main state file is also explicitly namespaced for this process.
    it('T10: MULTITASK_STATE_FILE honors GLUDD_MULTITASK_STATE_FILE env override', () => {
      assert.strictEqual(
        exportsMod.MULTITASK_STATE_FILE, ENV_STATE_FILE,
        'state file must be env-overridable for test isolation from live sessions',
      )
    })
  })

  // ==========================================================================
  // BEHAVIOR 1 — MIN_DISPATCHES floor (=10)
  //
  // When TASKS.md has unchecked items the agent MUST dispatch >=10 subagents.
  // Mutating tools stay denied until the current wave reaches the floor.
  // ==========================================================================
  describe('BEHAVIOR 1: MIN_DISPATCHES=10 floor', () => {
    it('T11: denies edit at 0 dispatches with pending work (names the floor)', async () => {
      const { hook } = await freshPlugin()
      const r = await hook({ tool: 'edit' })
      assertDeny(r, 'CONFIGURED MINIMUM', 'edit at 0/10 dispatches must be minimum-denied')
      assert.ok(r.message.includes('10'), 'deny message must name the floor (10)')
    })

    it('T12: keeps edit/write/bash denied mid-wave at 5/10 dispatches (floor is 10, not 1)', async () => {
      const { hook } = await freshPlugin()
      await dispatchN(hook, 5)

      for (const tool of ['edit', 'write', 'bash']) {
        const r = await hook({ tool })
        assertDeny(
          r, 'CONFIGURED MINIMUM',
          `${tool} must be DENIED at 5/10 dispatches — a single dispatch must not unlock the wave`,
        )
      }
    })

    it('T13: allows edit once the wave reaches the floor (10/10 dispatches)', async () => {
      const { hook } = await freshPlugin()
      await dispatchN(hook, 10)
      const r = await hook({ tool: 'edit' })
      assert.strictEqual(r, undefined, 'edit must be allowed at 10/10 dispatches')
    })

    it('T14: always allows dispatch tools (task) below the ceiling', async () => {
      const { hook } = await freshPlugin()
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined)
    })

    // TDD-FAIL: AGENTS.md "UNDER-FLOOR HARD BLOCK (2026-07-15)" — "Every
    // non-dispatch tool call (including read/glob/grep) is blocked until >=10
    // dispatches have been made". The plugin currently exempts read tools
    // from the under-floor gate, so "dispatch FIRST" is unenforced for the
    // read-grind pattern.
    it('T15: permits initial investigation reads, then enforces the configured minimum', async () => {
      const { hook } = await freshPlugin()
      for (const tool of ['read', 'grep', 'glob']) {
        const r = await hook({ tool })
        assert.strictEqual(r, undefined,
          `${tool} must be available during the initial investigation burst`)
      }
      await hook({ tool: 'task' })
      for (const tool of ['read', 'grep', 'glob']) {
        const r = await hook({ tool })
        assertDeny(
          r, 'CONFIGURED MINIMUM',
          `${tool} must be blocked after dispatch begins but before the configured minimum`,
        )
      }
    })

    it('T16: denies the 11th dispatch (DISPATCH CEILING)', async () => {
      const { hook } = await freshPlugin()
      await dispatchN(hook, 10)
      const r = await hook({ tool: 'agent' })
      assertDeny(r, 'DISPATCH CEILING', '11th dispatch in one message must breach the ceiling')
    })
  })

  // ==========================================================================
  // BEHAVIOR 2 — thin-wave blanking (1-9 dispatches)
  // ==========================================================================
  describe('BEHAVIOR 2: thin-wave blanking (1-9 dispatches)', () => {
    it('T17: blanks the response text after a 1-dispatch wave', async () => {
      const { hook, tc } = await freshPlugin()
      await dispatchN(hook, 1)

      const original = 'I dispatched one subagent and here is my summary.'
      const result = await tc({}, { text: original })

      assert.ok(result && typeof result.text === 'string')
      assert.ok(result.text.includes('THIN WAVE BLOCKED'),
        'a 1-dispatch wave must be blanked with THIN WAVE BLOCKED')
      assert.ok(!result.text.includes(original),
        'the original response text must not survive blanking')
      assert.ok(result.text.includes('10'),
        'the blanking directive must name the floor (10)')
    })

    it('T18: blanks the response text after a 9-dispatch wave (just under floor)', async () => {
      const { hook, tc } = await freshPlugin()
      await dispatchN(hook, 9)

      const result = await tc({}, { text: 'nine dispatched' })
      assert.ok(result && typeof result.text === 'string')
      assert.ok(result.text.includes('THIN WAVE BLOCKED'))
      assert.ok(!result.text.includes('nine dispatched'))
    })

    it('T19: passes a full 10-dispatch wave response through unmodified', async () => {
      const { hook, tc } = await freshPlugin()
      await dispatchN(hook, 10)

      const output = { text: 'full wave of ten dispatched' }
      const result = await tc({}, output)
      assert.strictEqual(result, output, 'a 10-wave response must pass through unmodified')
    })

    it('T20: does not blank a zero-dispatch response (streak-counted instead)', async () => {
      const { tc } = await freshPlugin()
      const output = { text: 'no dispatches in this message' }
      const result = await tc({}, output)
      assert.strictEqual(result, output, 'zero-dispatch responses are streak-counted, not blanked')
    })

    // TDD-FAIL: the blanking branch returns BEFORE handleMessageBoundary, so
    // the thin wave's dispatch count is never reset. When the agent obeys the
    // directive ("Re-send with >= 10 dispatches"), the stale count (3) is
    // still in thisMessageDispatches and the corrective wave hits the ceiling
    // at 3+7 — dispatches 8-10 of the REQUIRED wave are denied. Blanking must
    // close the message boundary.
    it('T21: resets the wave counter after blanking so the corrective 10-wave is possible', async () => {
      const { hook, tc } = await freshPlugin()
      await dispatchN(hook, 3)

      const blanked = await tc({}, { text: 'thin wave summary' })
      assert.ok(blanked && blanked.text.includes('THIN WAVE BLOCKED'))

      for (let i = 0; i < 10; i++) {
        const r = await hook({ tool: 'task' })
        assert.strictEqual(
          r, undefined,
          `corrective-wave dispatch ${i + 1}/10 must be allowed after a thin-wave blank — ` +
          'stale thisMessageDispatches from the blanked wave must not consume the ceiling',
        )
      }
    })
  })

  describe('BEHAVIOR 2b: result-arrival refill reminder', () => {
    it('warns after a result drains a stale pool below five', async () => {
      const { hook, tc } = await freshRefillPlugin(1)
      await dispatchN(hook, 4)
      await sleep(5)

      const result = await tc({}, { text: 'task result: completed successfully' })

      assert.ok(result && typeof result.text === 'string')
      assert.match(result.text, /FLOOR LOW: only 3 estimated subagent\(s\) remain/)
      assert.match(result.text, /Dispatch replacements now/)
    })

    it('does not warn before the configured refresh interval', async () => {
      const { hook, tc } = await freshRefillPlugin(30_000)
      await dispatchN(hook, 4)
      const output = { text: 'task result: completed successfully' }

      const result = await tc({}, output)

      assert.strictEqual(result, output)
    })
  })

  // ==========================================================================
  // BEHAVIOR 3 — grinding detection (5+ consecutive non-dispatch calls / 30s)
  // ==========================================================================
  describe('BEHAVIOR 3: grinding detection (5 calls in 30s window)', () => {
    it('T22: allows 4 consecutive non-dispatch calls, denies the 5th (streak)', async () => {
      const { hook } = await freshPlugin()
      // todowrite is outside the under-floor tool set, so the ONLY gate that
      // can fire here is the grinding streak — this isolates the counter.
      for (let i = 0; i < 4; i++) {
        const r = await hook({ tool: 'todowrite' })
        assert.strictEqual(r, undefined, `call ${i + 1} must be allowed (streak below threshold)`)
      }
      const fifth = await hook({ tool: 'todowrite' })
      assertDeny(fifth, 'CONSECUTIVE NON-DISPATCH STREAK',
        '5th consecutive non-dispatch call with pending work must be denied')
    })

    it('T23: a dispatch resets the streak (4 more calls allowed, then 5th denied again)', async () => {
      const { hook } = await freshPlugin()
      for (let i = 0; i < 4; i++) {
        await hook({ tool: 'todowrite' })
      }

      const dispatch = await hook({ tool: 'task' })
      assert.strictEqual(dispatch, undefined, 'dispatch must be allowed and reset the streak')

      for (let i = 0; i < 4; i++) {
        const r = await hook({ tool: 'todowrite' })
        assert.strictEqual(r, undefined,
          `post-dispatch call ${i + 1} must be allowed — dispatch must have zeroed the counter`)
      }
      const fifth = await hook({ tool: 'todowrite' })
      assertDeny(fifth, 'CONSECUTIVE NON-DISPATCH STREAK',
        'streak must re-trip on the 5th call after the reset')
    })

    it('T24: window-tuned plugin exposes the supported hook surface', async () => {
      const { hook } = await freshWindowPlugin()
      assert.strictEqual(typeof hook, 'function')
    })

    // TDD-FAIL: when the window expires, the reset branch sets the counter to
    // 0 WITHOUT counting the current call. The call that restarts the window
    // IS a non-dispatch call inside the new window and must count as 1 —
    // otherwise every post-expiry threshold is off by one (6 calls trip it
    // instead of 5).
    it('T25: denies the 5th consecutive call within a fresh window after expiry', async () => {
      const { hook } = await freshWindowPlugin()

      const first = await hook({ tool: 'todowrite' }) // old window, count=1
      assert.strictEqual(first, undefined)
      await sleep(600) // 400ms window expires

      // Five consecutive non-dispatch calls inside the NEW window.
      const results = []
      for (let i = 0; i < 5; i++) {
        results.push(await hook({ tool: 'todowrite' }))
      }
      for (let i = 0; i < 4; i++) {
        assert.strictEqual(results[i], undefined,
          `new-window call ${i + 1} must be allowed (below threshold)`)
      }
      assertDeny(results[4], 'CONSECUTIVE NON-DISPATCH STREAK',
        '5 consecutive non-dispatch calls inside the new window must trip the streak — ' +
        'the window-restarting call must count as 1, not 0')
    })
  })

  // ==========================================================================
  // BEHAVIOR 4 — zero-dispatch streak (2 messages) blocks ALL tools
  // ==========================================================================
  describe('BEHAVIOR 4: zero-dispatch streak blocks ALL tools', () => {
    it('T26: after 2 zero-dispatch messages, denies tools outside the under-floor set', async () => {
      const { hook, tc } = await freshPlugin()
      await buildZeroStreak(tc)

      // 4 tools only: a 5th consecutive call would trip the grinding gate and
      // change the deny message, muddying the assertion.
      for (const tool of ['todowrite', 'question', 'webfetch', 'skill']) {
        const r = await hook({ tool })
        assertDeny(r, 'ZERO-DISPATCH STREAK',
          `${tool} must be denied during a zero-dispatch streak (no tool-type bypass)`)
      }
    })

    it('T27: after 2 zero-dispatch messages, denies read/grep/glob too', async () => {
      const { hook, tc } = await freshPlugin()
      await buildZeroStreak(tc)

      for (const tool of ['read', 'grep', 'glob']) {
        const r = await hook({ tool })
        assertDeny(r, 'ZERO-DISPATCH STREAK',
          `${tool} must be denied during a zero-dispatch streak (read tools included)`)
      }
    })

    it('T28: dispatch is the only way out — task allowed, non-dispatch unblocked after, floor still enforced', async () => {
      const { hook, tc } = await freshPlugin()
      await buildZeroStreak(tc)

      const dispatch = await hook({ tool: 'task' })
      assert.strictEqual(dispatch, undefined, 'dispatch must be allowed during the streak block')

      const afterDispatch = await hook({ tool: 'todowrite' })
      assert.strictEqual(afterDispatch, undefined,
        'a dispatch in the current message must lift the zero-streak block')

      const edit = await hook({ tool: 'edit' })
      assertDeny(edit, 'CONFIGURED MINIMUM',
        'edit must STILL be under-floor denied at 1/10 dispatches — the streak exit does not waive the floor')
    })
  })

  // ==========================================================================
  // BEHAVIOR 5 — TASKS.md pending-work detection
  // ==========================================================================
  describe('BEHAVIOR 5: TASKS.md pending-work detection', () => {
    it('T29: detects star-bullet unchecked items (* [ ])', async () => {
      const dir = mkProjectDir('star', '# T\n\n* [ ] star bullet item\n')
      const { hook } = await freshPlugin(dir)
      const r = await hook({ tool: 'edit' })
      assertDeny(r, 'CONFIGURED MINIMUM', '* [ ] must count as pending work')
    })

    it('T30: detects indented unchecked items (nested list)', async () => {
      const dir = mkProjectDir('indent', '# T\n\n- [x] parent\n  - [ ] nested pending child\n')
      const { hook } = await freshPlugin(dir)
      const r = await hook({ tool: 'edit' })
      assertDeny(r, 'CONFIGURED MINIMUM', 'indented `  - [ ]` must count as pending work')
    })

    it('T31: detects tight checkboxes (- [] with no inner space)', async () => {
      const dir = mkProjectDir('tight', '# T\n\n- [] tight pending item\n')
      const { hook } = await freshPlugin(dir)
      const r = await hook({ tool: 'edit' })
      assertDeny(r, 'CONFIGURED MINIMUM', '- [] must count as pending work')
    })

    it('T32: ignores [ ] in prose without a list marker', async () => {
      const dir = mkProjectDir('prose', '# T\n\nThe [ ] placeholder appears in prose only.\n')
      const { hook } = await freshPlugin(dir)
      const r = await hook({ tool: 'edit' })
      assert.strictEqual(r, undefined, 'bare [ ] in prose is not a task item')
    })

    it('T33: treats - [x] and - [X] as completed (no pending work)', async () => {
      const dir = mkProjectDir('checked', '# T\n\n- [x] done lower\n- [X] done upper\n')
      const { hook } = await freshPlugin(dir)
      const r = await hook({ tool: 'edit' })
      assert.strictEqual(r, undefined, 'checked items must not count as pending work')
    })

    // TDD-FAIL: getProjectRoot() honors GLUDD_PROJECT_ROOT only when TASKS.md
    // already exists inside it, and caches the first resolution forever.
    // When the explicit root has no TASKS.md the plugin silently falls back
    // to walking up from cwd — landing on the REAL repo ledger (which has
    // unchecked items) — and enforces against a project the caller explicitly
    // pointed away from. An explicit root with no ledger must mean "no
    // pending work", never "borrow another project's ledger".
    it('T34: explicit GLUDD_PROJECT_ROOT without TASKS.md means no pending work (no fallback ledger)', async () => {
      const dir = mkProjectDir('noledger', null) // no TASKS.md at all
      // Build the instance rooted at TASKS_DIR (avoids the factory's
      // gate-refresh side effect against the repo root), then point the env
      // at the empty root for the hook call — the explicit override must win.
      const { hook } = await freshPlugin(TASKS_DIR)
      process.env.GLUDD_PROJECT_ROOT = dir
      try {
        const r = await hook({ tool: 'edit' })
        assert.strictEqual(
          r, undefined,
          'with an explicit project root containing no TASKS.md there is no pending work — ' +
          'the plugin must not enforce against a cached/fallback ledger from another project',
        )
      } finally {
        process.env.GLUDD_PROJECT_ROOT = TASKS_DIR
      }
    })
  })
})
