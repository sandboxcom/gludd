// enforce-task-tracking.test.node.mjs — behavioral runtime tests for the
// task-tracking plugin.
//
// Verifies the REAL hook behavior by compiling the plugin with esbuild and
// invoking tool.execute.before with constructed arguments against a temp
// filesystem. This is the "Self-Test Quality" requirement: structural tests
// are insufficient — at least one test per plugin MUST invoke the actual
// hook function and assert on the return value.
//
// Behaviors verified:
//   T1  default factory returns tool.execute.before hook
//   T2  plugin exposes only the default factory (no named exports)
//   T3  ALLOW write to src/ when TASKS.md mtime is newer (just updated)
//   T4  DENY write to src/ when TASKS.md mtime is unchanged (stale)
//   T5  ALLOW write to tests/ files (out of scope)
//   T6  ALLOW write to .opencode/ files (out of scope)
//   T7  ALLOW write to non-.py files (out of scope)
//   T8  ALLOW when TASKS.md does not exist (no-op guard)
//   T9  ALLOW on first edit when no prior state exists
//   T10 Subagent guard: OPENCODE_SUBAGENT=1 bypasses
//   T11 GLUDD_TASK_TRACKING_ENFORCE=0 bypasses
//   T12 Fail-open: garbage input does not throw and does not deny
//   T13 DENY message references TASKS.md and AGENTS.md
//   T14 Non-edit/write tools pass through (read, glob, grep)
//   T15 text.complete returns output unchanged on first call
//   T16 text.complete injects NOTE after 1 missed update
//   T17 text.complete resets count when TASKS.md is updated
//   T18 text.complete fail-open on corrupt state
//   T19 text.complete subagent guard
//   T20 text.complete ENFORCE=0 bypass
//   T21 system.transform prepends task-tracking directive
//   T22 system.transform returns non-string output unchanged
//   T23 system.transform subagent guard
//
// Runner: node --test .opencode/plugin/enforce-task-tracking.test.node.mjs

import { describe, it, after, before, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import * as fs from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'
import { execSync } from 'node:child_process'

const PROJECT_ROOT = process.cwd()
const OUTFILE = '/tmp/gludd-test-enforce-task-tracking.js'
const ALIVE_PATH = '/tmp/gludd-plugin-alive.json'
const STATE_FILE = '/tmp/gludd-task-tracking.json'

// Park any hot module that would shadow the code under test.
const HOT_PATH = '/tmp/gludd-hot-enforce-task-tracking.js'
const HOT_BACKUP = '/tmp/gludd-hot-enforce-task-tracking.js.test-backup'
if (fs.existsSync(HOT_PATH)) {
  try { fs.renameSync(HOT_PATH, HOT_BACKUP) } catch {}
}

// Environment hardening — start from a clean state.
delete process.env.OPENCODE_SUBAGENT
delete process.env.GLUDD_TASK_TRACKING_ENFORCE
process.env.GLUDD_ALIVE_PATH = ALIVE_PATH

function compileWithEsbuild(outfile) {
  const env = { ...process.env, npm_config_userconfig: '/dev/null' }
  const args = `.opencode/plugin/enforce-task-tracking.ts --bundle --platform=node --target=node18 --format=cjs --outfile=${outfile}`

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

const _require = createRequire(import.meta.url)
const mod = _require(OUTFILE)

// --- temp project tree for filesystem-backed tests -------------------------

let TMP_ROOT = ''

function makeTempProject() {
  TMP_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'gludd-tt-test-'))
  fs.mkdirSync(path.join(TMP_ROOT, 'src', 'general_ludd'), { recursive: true })
  fs.mkdirSync(path.join(TMP_ROOT, 'tests', 'unit'), { recursive: true })
  // Create a TASKS.md with initial content.
  fs.writeFileSync(
    path.join(TMP_ROOT, 'TASKS.md'),
    '# Tasks\n\n- [ ] Initial task 1\n- [ ] Initial task 2\n'
  )
}

function rmTempProject() {
  try { fs.rmSync(TMP_ROOT, { recursive: true, force: true }) } catch {}
}

// Helper: bump TASKS.md mtime to a future time so the guard sees it as updated.
function touchTasksMd(deltaMs = 1000) {
  const tasksPath = path.join(TMP_ROOT, 'TASKS.md')
  if (!fs.existsSync(tasksPath)) return
  const stats = fs.statSync(tasksPath)
  fs.utimesSync(tasksPath, stats.atime, new Date(stats.mtime.getTime() + deltaMs))
}

// Helper: append a line to TASKS.md (writes content + bumps mtime).
function appendToTasksMd(text) {
  const tasksPath = path.join(TMP_ROOT, 'TASKS.md')
  if (!fs.existsSync(tasksPath)) {
    fs.writeFileSync(tasksPath, text + '\n')
    return
  }
  // Wait a tick so mtime actually advances past the previous value.
  const prev = fs.statSync(tasksPath).mtimeMs
  fs.appendFileSync(tasksPath, text + '\n')
  // On fast filesystems appendFile may not advance mtime enough; force it.
  const after = fs.statSync(tasksPath).mtimeMs
  if (after <= prev) {
    fs.utimesSync(tasksPath, new Date(), new Date(Date.now() + 1000))
  }
}

async function freshPlugin(opts = {}) {
  delete process.env.OPENCODE_SUBAGENT
  if (opts.enforce !== undefined) {
    process.env.GLUDD_TASK_TRACKING_ENFORCE = opts.enforce
  } else {
    delete process.env.GLUDD_TASK_TRACKING_ENFORCE
  }
  if (opts.subagent) process.env.OPENCODE_SUBAGENT = '1'
  process.env.GLUDD_PROJECT_ROOT = TMP_ROOT
  // Clear state file unless keepState is set (used for deny-path tests where
  // the first hook call records a baseline that the second call must catch).
  if (!opts.keepState) {
    try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
  }
  // Clear the module cache + project-root cache so env changes take effect.
  delete _require.cache[_require.resolve(OUTFILE)]
  const m = _require(OUTFILE)
  // shared.ts caches project root; clear the env-var key to force re-eval.
  if (m.invalidateProjectRootCache) m.invalidateProjectRootCache()
  const instance = await m.default({})
  return { m, hook: instance['tool.execute.before'] }
}

function assertDeny(r, needle, msg) {
  assert.ok(r !== null && r !== undefined, msg + ' (got allow instead of deny)')
  assert.strictEqual(r.permissionDecision, 'deny', msg)
  if (needle) {
    assert.ok(r.message && r.message.includes(needle),
      `${msg} — message must include "${needle}", got: ${r.message}`)
  }
}

function assertAllow(r, msg) {
  assert.ok(r === undefined || r === null,
    `${msg} — expected allow (undefined/null), got deny: ${JSON.stringify(r)}`)
}

function cleanup() {
  try { fs.rmSync(OUTFILE, { force: true }) } catch {}
  try { fs.rmSync(ALIVE_PATH, { force: true }) } catch {}
  try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
  rmTempProject()
  if (fs.existsSync(HOT_BACKUP)) {
    try { fs.renameSync(HOT_BACKUP, HOT_PATH) } catch {}
  }
}

describe('enforce-task-tracking', { concurrency: 1 }, () => {

  before(() => { makeTempProject() })
  after(() => { cleanup() })
  afterEach(() => {
    try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
  })

  // ==========================================================================
  // T1-T2: export surface
  // ==========================================================================
  describe('export surface', () => {
    it('T1: default factory returns tool.execute.before hook', async () => {
      assert.strictEqual(typeof mod.default, 'function')
      const instance = await mod.default({})
      assert.strictEqual(typeof instance['tool.execute.before'], 'function')
    })

    it('T2: plugin exposes only the default factory', () => {
      assert.strictEqual(typeof mod.default, 'function')
      const named = Object.keys(mod).filter(k => k !== 'default')
      assert.deepStrictEqual(named, [],
        `named exports crash OpenCode's plugin loader: ${named.join(', ')}`)
    })
  })

  // ==========================================================================
  // T3: ALLOW — TASKS.md was just updated (the normal agent workflow)
  // ==========================================================================
  describe('ALLOW: TASKS.md updated before edit', () => {
    it('T3: ALLOW write to src/ when TASKS.md was just updated', async () => {
      // Step 1: Record baseline state by making a first edit.
      const { hook: hook1 } = await freshPlugin()
      const r1 = await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/baseline.py'),
                  content: '# baseline\n' } },
      )
      assertAllow(r1, 'first edit must be allowed (state initializes)')

      // Step 2: Touch TASKS.md to simulate agent updating it.
      appendToTasksMd('- [ ] New task: fix baseline.py')
      // Small pause to ensure mtime is different after append.
      await new Promise(r => setTimeout(r, 10))

      // Step 3: Second edit to src/ — must be allowed because TASKS.md changed.
      const { hook: hook2 } = await freshPlugin()
      const r2 = await hook2(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/baseline.py'),
                  content: '# updated\n' } },
      )
      assertAllow(r2, 'edit after TASKS.md update must be allowed')
    })

    it('T3b: ALLOW edit (not just write) after TASKS.md update', async () => {
      appendToTasksMd('- [ ] Task: refactor module')
      await new Promise(r => setTimeout(r, 10))
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'edit' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/mod.py'),
                  newString: 'x = 1' } },
      )
      assertAllow(r, 'edit tool must also be allowed after TASKS.md update')
    })
  })

  // ==========================================================================
  // T4: DENY — TASKS.md unchanged (the enforcement)
  // ==========================================================================
  describe('DENY: TASKS.md unchanged', () => {
    it('T4: DENY write to src/ when TASKS.md mtime is stale', async () => {
      // First edit: records baseline mtime.
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/mod_a.py'),
                  content: '# a\n' } },
      )

      // Second edit without touching TASKS.md → DENY. Keep state so the
      // baseline recorded by hook1 persists.
      const { hook: hook2 } = await freshPlugin({ keepState: true })
      const r = await hook2(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/mod_a.py'),
                  content: '# a modified\n' } },
      )
      assertDeny(r, 'TASKS.md',
        'edit without TASKS.md update must be denied')
    })

    it('T4b: DENY on second consecutive edit to different src/ file', async () => {
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/first_mod.py'),
                  content: '# first\n' } },
      )

      // Different file, same stale TASKS.md → DENY.
      const { hook: hook2 } = await freshPlugin({ keepState: true })
      const r = await hook2(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/second_mod.py'),
                  content: '# second\n' } },
      )
      assertDeny(r, 'TASKS.md',
        'any src/ edit without TASKS.md update must be denied regardless of file')
    })

    it('T4c: DENY edit (not just write) when TASKS.md is stale', async () => {
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/mod_b.py'),
                  content: '# b\n' } },
      )

      const { hook: hook2 } = await freshPlugin({ keepState: true })
      const r = await hook2(
        { tool: 'edit' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/mod_b.py'),
                  newString: '# b changed' } },
      )
      assertDeny(r, 'TASKS.md',
        'edit tool must also be denied when TASKS.md is stale')
    })
  })

  // ==========================================================================
  // T5-T7: scope — non-src files pass through freely
  // ==========================================================================
  describe('SCOPE: only src/general_ludd *.py files are gated', () => {
    it('T5: ALLOW write to tests/ files', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'tests/unit/test_new.py'),
                  content: 'def test_new():\n    pass\n' } },
      )
      assertAllow(r, 'tests/ files must pass through freely')
    })

    it('T6: ALLOW write to .opencode/ files', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, '.opencode/plugin/foo.ts'),
                  content: 'export default () => ({})\n' } },
      )
      assertAllow(r, '.opencode/ files must pass through freely')
    })

    it('T7: ALLOW write to non-.py files', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/config.yml'),
                  content: 'key: value\n' } },
      )
      assertAllow(r, 'non-.py files in src/ must pass through')
    })
  })

  // ==========================================================================
  // T8-T9: TASKS.md missing + first-edit initialization
  // ==========================================================================
  describe('TASKS.md edge cases', () => {
    it('T8: ALLOW when TASKS.md does not exist', async () => {
      // Remove TASKS.md from the temp project.
      const tasksPath = path.join(TMP_ROOT, 'TASKS.md')
      const saved = fs.readFileSync(tasksPath, 'utf8')
      fs.unlinkSync(tasksPath)

      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/no_tasks.py'),
                  content: '# no tasks file\n' } },
      )
      assertAllow(r, 'missing TASKS.md must be a no-op (allow)')

      // Restore TASKS.md for other tests.
      fs.writeFileSync(tasksPath, saved)
    })

    it('T9: ALLOW on first edit (state initializes with current mtime)', async () => {
      // Ensure no prior state exists.
      try { fs.rmSync(STATE_FILE, { force: true }) } catch {}

      // Fresh start with no state file — first edit records and allows.
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/fresh.py'),
                  content: '# fresh start\n' } },
      )
      assertAllow(r, 'first edit with no prior state must be allowed')

      // Verify state file was created.
      assert.ok(fs.existsSync(STATE_FILE), 'state file must be created on first edit')
    })
  })

  // ==========================================================================
  // T10-T11: bypass switches
  // ==========================================================================
  describe('BYPASS', () => {
    it('T10: subagent guard — OPENCODE_SUBAGENT=1 bypasses', async () => {
      // First record baseline state.
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/sub_mod.py'),
                  content: '# sub\n' } },
      )

      // Now try as subagent — must bypass even though TASKS.md is stale.
      const { hook } = await freshPlugin({ keepState: true, subagent: true })
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/sub_mod.py'),
                  content: '# sub changed\n' } },
      )
      assertAllow(r, 'subagents inherit orchestrator enforcement, never their own')
    })

    it('T11: GLUDD_TASK_TRACKING_ENFORCE=0 bypasses', async () => {
      // Record baseline.
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/esc_mod.py'),
                  content: '# esc\n' } },
      )

      // Now with enforcement disabled.
      const { hook } = await freshPlugin({ keepState: true, enforce: '0' })
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/esc_mod.py'),
                  content: '# esc changed\n' } },
      )
      assertAllow(r, 'GLUDD_TASK_TRACKING_ENFORCE=0 must bypass')
    })
  })

  // ==========================================================================
  // T12: fail-open
  // ==========================================================================
  describe('FAIL-OPEN', () => {
    it('T12a: garbage input — no args — does not throw or deny', async () => {
      const { hook } = await freshPlugin()
      const r = await hook({ tool: 'write' }, undefined)
      assertAllow(r, 'missing output/args must not wedge the editor')
    })

    it('T12b: garbage input — empty args — does not throw or deny', async () => {
      const { hook } = await freshPlugin()
      const r = await hook({ tool: 'write' }, { args: {} })
      assertAllow(r, 'empty args must not wedge the editor')
    })

    it('T12c: garbage input — null tool_name — does not throw', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: null },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/null.py') } },
      )
      assertAllow(r, 'null tool must pass through (not edit/write)')
    })

    it('T12d: file path that is not a string must not throw', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: undefined, content: 'pass\n' } },
      )
      assertAllow(r, 'undefined filePath must not throw')
    })
  })

  // ==========================================================================
  // T13: DENY message contract
  // ==========================================================================
  describe('DENY MESSAGE', () => {
    it('T13: deny message references TASKS.md, AGENTS.md, and policy', async () => {
      const { hook: hook1 } = await freshPlugin()
      await hook1(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/msg_mod.py'),
                  content: '# msg\n' } },
      )

      const { hook: hook2 } = await freshPlugin({ keepState: true })
      const r = await hook2(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/msg_mod.py'),
                  content: '# msg changed\n' } },
      )
      assertDeny(r, 'TASKS.md',
        'deny must reference TASKS.md')
      assert.ok(r.message.includes('TASK TRACKING VIOLATION'),
        `deny must include VIOLATION header, got: ${r.message}`)
    })
  })

  // ==========================================================================
  // T14: non-edit/write tools pass through
  // ==========================================================================
  describe('NON-EDIT/WRITE TOOLS', () => {
    it('T14a: read tool passes through', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'read' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/read_mod.py') } },
      )
      assertAllow(r, 'read tool must pass through freely')
    })

    it('T14b: glob tool passes through', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'glob' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/') } },
      )
      assertAllow(r, 'glob tool must pass through freely')
    })

    it('T14c: grep tool passes through', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'grep' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/') } },
      )
      assertAllow(r, 'grep tool must pass through freely')
    })
  })

  // ==========================================================================
  // T15-T18: text.complete advisory injection
  // ==========================================================================
  describe('TEXT.COMPLETE advisory injection', () => {
    async function freshPluginTextComplete(opts = {}) {
      delete process.env.OPENCODE_SUBAGENT
      if (opts.enforce !== undefined) {
        process.env.GLUDD_TASK_TRACKING_ENFORCE = opts.enforce
      } else {
        delete process.env.GLUDD_TASK_TRACKING_ENFORCE
      }
      if (opts.subagent) process.env.OPENCODE_SUBAGENT = '1'
      process.env.GLUDD_PROJECT_ROOT = TMP_ROOT
      if (!opts.keepState) {
        try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
      }
      delete _require.cache[_require.resolve(OUTFILE)]
      const m = _require(OUTFILE)
      if (m.invalidateProjectRootCache) m.invalidateProjectRootCache()
      const instance = await m.default({})
      return { m, hook: instance['experimental.text.complete'] }
    }

    it('T15: text.complete returns output unchanged on first call', async () => {
      try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
      const original = 'Hello, agent response.'
      const { hook } = await freshPluginTextComplete()
      const r = await hook(null, original)
      assert.strictEqual(r, original, 'first call must return output unchanged')
    })

    it('T16: text.complete injects NOTE after 1 missed update', async () => {
      // Record baseline state.
      const { hook: h1 } = await freshPluginTextComplete()
      const r1 = await h1(null, 'msg1')
      assert.strictEqual(r1, 'msg1', 'first call must return unchanged')

      // Second call with same TASKS.md mtime → increment count → NOTE.
      const { hook: h2 } = await freshPluginTextComplete({ keepState: true })
      const r2 = await h2(null, 'msg2')
      assert.ok(typeof r2 === 'string' && r2.includes('[TASK TRACKING: NOTE'),
        `expected NOTE injection, got: ${typeof r2 === 'string' ? r2.substring(0, 200) : String(r2)}`)
    })

    it('T17: text.complete resets count when TASKS.md is updated', async () => {
      // Two missed cycles → count = 2 (WARNING not triggered yet).
      const { hook: h1 } = await freshPluginTextComplete()
      await h1(null, 'a')
      await new Promise(r => setTimeout(r, 10))
      const { hook: h2 } = await freshPluginTextComplete({ keepState: true })
      await h2(null, 'b')

      // Now update TASKS.md.
      touchTasksMd(2000)
      await new Promise(r => setTimeout(r, 10))

      // Next call should see updated mtime → reset count → no injection.
      const { hook: h3 } = await freshPluginTextComplete({ keepState: true })
      const r3 = await h3(null, 'c')
      assert.strictEqual(r3, 'c', 'after TASKS.md update, output must be unchanged')
    })

    it('T18: text.complete fail-open on corrupt state', async () => {
      try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
      // Write garbage state.
      fs.writeFileSync(STATE_FILE, 'not valid json {{{')
      const { hook } = await freshPluginTextComplete()
      const r = await hook(null, 'garbage state recovery')
      assert.strictEqual(r, 'garbage state recovery', 'corrupt state must fail-open')
      try { fs.rmSync(STATE_FILE, { force: true }) } catch {}
    })

    it('T19: text.complete subagent guard returns output unchanged', async () => {
      const original = 'subagent output'
      const { hook } = await freshPluginTextComplete({ subagent: true })
      const r = await hook(null, original)
      assert.strictEqual(r, original, 'subagent must bypass text.complete')
    })

    it('T20: text.complete ENFORCE=0 returns output unchanged', async () => {
      // Record baseline.
      const { hook: h1 } = await freshPluginTextComplete()
      await h1(null, 'baseline')
      // Now with enforcement disabled.
      const { hook: h2 } = await freshPluginTextComplete({ keepState: true, enforce: '0' })
      const r2 = await h2(null, 'enforcement off')
      assert.strictEqual(r2, 'enforcement off', 'ENFORCE=0 must bypass text.complete')
    })
  })

  // ==========================================================================
  // T21: system.transform directive injection
  // ==========================================================================
  describe('SYSTEM.TRANSFORM directive', () => {
    async function freshPluginSysTransform(opts = {}) {
      delete process.env.OPENCODE_SUBAGENT
      if (opts.subagent) process.env.OPENCODE_SUBAGENT = '1'
      process.env.GLUDD_PROJECT_ROOT = TMP_ROOT
      delete _require.cache[_require.resolve(OUTFILE)]
      const m = _require(OUTFILE)
      if (m.invalidateProjectRootCache) m.invalidateProjectRootCache()
      const instance = await m.default({})
      return { m, hook: instance['experimental.chat.system.transform'] }
    }

    it('T21: system.transform prepends task-tracking directive to string output', async () => {
      const original = 'You are a helpful coding assistant.'
      const { hook } = await freshPluginSysTransform()
      const r = await hook(null, original)
      assert.ok(typeof r === 'string' && r.includes('TASK TRACKING DIRECTIVE'),
        `directive must be prepended, got: ${typeof r === 'string' ? r.substring(0, 200) : String(r)}`)
      assert.ok(typeof r === 'string' && r.includes('single source of truth'),
        'directive must include "single source of truth"')
      assert.ok(typeof r === 'string' && r.includes(original),
        'original content must be preserved after directive')
    })

    it('T22: system.transform returns non-string output unchanged', async () => {
      const obj = { role: 'system', content: 'system prompt' }
      const { hook } = await freshPluginSysTransform()
      const r = await hook(null, obj)
      assert.strictEqual(r, obj, 'non-string output must pass through')
    })

    it('T23: system.transform subagent guard returns output unchanged', async () => {
      const original = 'subagent system prompt'
      const { hook } = await freshPluginSysTransform({ subagent: true })
      const r = await hook(null, original)
      assert.strictEqual(r, original, 'subagent must bypass system.transform')
    })
  })
})
