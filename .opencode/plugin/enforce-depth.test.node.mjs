// enforce-depth.test.node.mjs — behavioral tests for the depth enforcement plugin.
//
// Verifies six enforcement behaviors:
//   1. Deny task dispatch at MAX_DEPTH (3)
//   2. Allow task dispatch below MAX_DEPTH
//   3. Non-dispatch tools (read) pass through at max depth
//   4. Subagent guard: OPENCODE_SUBAGENT=1 bypasses enforcement
//   5. GLUDD_DEPTH_ENFORCE=0 bypasses enforcement
//   6. reportAlive side-effect: writes plugin-alive.json entry
//
// Compilation: esbuild --bundle to CJS, then createRequire.
// Runner: node --test .opencode/plugin/enforce-depth.test.node.mjs

import { describe, it, after } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import * as fs from 'node:fs'
import { execSync } from 'node:child_process'

const PROJECT_ROOT = process.cwd()
const OUTFILE = '/tmp/gludd-test-enforce-depth.js'
const ALIVE_PATH = '/tmp/gludd-plugin-alive.json'

// Park any hot module that would shadow the code under test.
const HOT_PATH = '/tmp/gludd-hot-depth.js'
const HOT_BACKUP = '/tmp/gludd-hot-depth.js.test-backup'
if (fs.existsSync(HOT_PATH)) {
  try { fs.renameSync(HOT_PATH, HOT_BACKUP) } catch {}
}

// Environment hardening.
delete process.env.OPENCODE_SUBAGENT
delete process.env.OPENCODE_DEPTH
delete process.env.GLUDD_DEPTH_ENFORCE
delete process.env.GLUDD_MAX_DEPTH
process.env.GLUDD_ALIVE_PATH = ALIVE_PATH

function compileWithEsbuild(outfile) {
  const env = { ...process.env, npm_config_userconfig: '/dev/null' }
  const args = `.opencode/plugin/enforce-depth.ts --bundle --platform=node --target=node18 --format=cjs --outfile=${outfile}`

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

// --- per-test isolation helpers -------------------------------------------

function wipeAlive() {
  try { fs.rmSync(ALIVE_PATH, { force: true }) } catch {}
}

async function freshPlugin(depth = 0, enforce = '1') {
  delete process.env.OPENCODE_DEPTH
  process.env.GLUDD_DEPTH_ENFORCE = enforce
  if (depth >= 0) {
    process.env.OPENCODE_DEPTH = String(depth)
  }
  delete _require.cache[_require.resolve(OUTFILE)]
  const m = _require(OUTFILE)
  const instance = await m.default({})
  return {
    m,
    hook: instance['tool.execute.before'],
  }
}

function assertDeny(r, needle, msg) {
  assert.ok(r !== null && r !== undefined, msg + ' (got allow instead of deny)')
  assert.strictEqual(r.permissionDecision, 'deny', msg)
  if (needle) {
    assert.ok(r.message.includes(needle), `${msg} — message must include "${needle}", got: ${r.message}`)
  }
}

function cleanup() {
  try { fs.rmSync(OUTFILE, { force: true }) } catch {}
  try { fs.rmSync(ALIVE_PATH, { force: true }) } catch {}
  if (fs.existsSync(HOT_BACKUP)) {
    try { fs.renameSync(HOT_BACKUP, HOT_PATH) } catch {}
  }
}

describe('enforce-depth', { concurrency: 1 }, () => {

  after(() => {
    cleanup()
  })

  // ==========================================================================
  // Export surface / constants
  // ==========================================================================
  describe('export surface', () => {
    it('T1: default factory returns tool.execute.before hook', async () => {
      assert.strictEqual(typeof mod.default, 'function')
      const instance = await mod.default({})
      assert.strictEqual(typeof instance['tool.execute.before'], 'function')
    })

    it('T2: MAX_DEPTH === 3', () => {
      assert.strictEqual(mod.MAX_DEPTH, 3)
    })

    it('T3: exports defaultImpl for testability', () => {
      assert.strictEqual(
        typeof mod.defaultImpl, 'object',
        'defaultImpl must be exported so tests can invoke real hook directly',
      )
    })
  })

  // ==========================================================================
  // BEHAVIOR 1 — Deny task dispatch at max depth
  // ==========================================================================
  describe('BEHAVIOR 1: deny dispatch at MAX_DEPTH', () => {
    it('T4: denies task dispatch at depth=3 (MAX_DEPTH)', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'task' })
      assertDeny(r, 'MAX DEPTH EXCEEDED', 'task dispatch at depth=3 must be denied')
      assert.ok(r.message.includes('depth=3'), 'deny message must include depth value')
      assert.ok(r.message.includes('limit=3'), 'deny message must include limit value')
    })

    it('T5: denies agent dispatch at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'agent' })
      assertDeny(r, 'MAX DEPTH EXCEEDED', 'agent dispatch at depth=3 must be denied')
    })

    it('T6: denies workflow dispatch at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'workflow' })
      assertDeny(r, 'MAX DEPTH EXCEEDED', 'workflow dispatch at depth=3 must be denied')
    })

    it('T7: denies dispatch at depth > MAX_DEPTH (depth=4)', async () => {
      const { hook } = await freshPlugin(4)
      const r = await hook({ tool: 'task' })
      assertDeny(r, 'MAX DEPTH EXCEEDED', 'task dispatch at depth=4 must be denied (above max)')
    })
  })

  // ==========================================================================
  // BEHAVIOR 2 — Allow dispatch below max depth
  // ==========================================================================
  describe('BEHAVIOR 2: allow dispatch below MAX_DEPTH', () => {
    it('T8: allows task dispatch at depth=0 (main agent)', async () => {
      const { hook } = await freshPlugin(0)
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined, 'task dispatch at depth=0 must be allowed')
    })

    it('T9: allows task dispatch at depth=1', async () => {
      const { hook } = await freshPlugin(1)
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined, 'task dispatch at depth=1 must be allowed')
    })

    it('T10: allows task dispatch at depth=2 (just below MAX_DEPTH)', async () => {
      const { hook } = await freshPlugin(2)
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined, 'task dispatch at depth=2 must be allowed')
    })

    it('T11: when OPENCODE_DEPTH is unset, defaults to depth=0 and allows dispatch', async () => {
      const { hook } = await freshPlugin(-1) // -1 signals "don't set the env var"
      delete process.env.OPENCODE_DEPTH
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined, 'unset OPENCODE_DEPTH must default to 0 and allow')
    })
  })

  // ==========================================================================
  // BEHAVIOR 3 — Non-dispatch tools pass through
  // ==========================================================================
  describe('BEHAVIOR 3: non-dispatch tools pass through at max depth', () => {
    it('T12: allows read at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'read' })
      assert.strictEqual(r, undefined, 'read tool must pass through at max depth')
    })

    it('T13: allows edit at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'edit' })
      assert.strictEqual(r, undefined, 'edit tool must pass through at max depth')
    })

    it('T14: allows write at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'write' })
      assert.strictEqual(r, undefined, 'write tool must pass through at max depth')
    })

    it('T15: allows bash at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: 'bash' })
      assert.strictEqual(r, undefined, 'bash tool must pass through at max depth')
    })

    it('T16: allows unknown/empty tool at depth=3', async () => {
      const { hook } = await freshPlugin(3)
      const r = await hook({ tool: '' })
      assert.strictEqual(r, undefined, 'empty tool must pass through')
    })
  })

  // ==========================================================================
  // BEHAVIOR 4 — Subagent guard (OPENCODE_SUBAGENT=1)
  // ==========================================================================
  describe('BEHAVIOR 4: subagent guard bypasses enforcement', () => {
    it('T17: OPENCODE_SUBAGENT=1 at depth=3 allows task dispatch', async () => {
      process.env.OPENCODE_SUBAGENT = '1'
      try {
        const { hook } = await freshPlugin(3)
        const r = await hook({ tool: 'task' })
        assert.strictEqual(r, undefined, 'subagent guard must bypass depth enforcement')
      } finally {
        delete process.env.OPENCODE_SUBAGENT
      }
    })

    it('T18: OPENCODE_SUBAGENT=1 at depth=4 allows agent dispatch', async () => {
      process.env.OPENCODE_SUBAGENT = '1'
      try {
        const { hook } = await freshPlugin(4)
        const r = await hook({ tool: 'agent' })
        assert.strictEqual(r, undefined, 'subagent guard must bypass at any depth')
      } finally {
        delete process.env.OPENCODE_SUBAGENT
      }
    })

    it('T19: OPENCODE_SUBAGENT=1 also allows non-dispatch tools (no regression)', async () => {
      process.env.OPENCODE_SUBAGENT = '1'
      try {
        const { hook } = await freshPlugin(3)
        const r = await hook({ tool: 'read' })
        assert.strictEqual(r, undefined, 'subagent guard should not interfere with read tools')
      } finally {
        delete process.env.OPENCODE_SUBAGENT
      }
    })
  })

  // ==========================================================================
  // BEHAVIOR 5 — GLUDD_DEPTH_ENFORCE=0 bypass
  // ==========================================================================
  describe('BEHAVIOR 5: GLUDD_DEPTH_ENFORCE=0 bypass', () => {
    it('T20: enforce=0 at depth=3 allows task dispatch', async () => {
      const { hook } = await freshPlugin(3, '0')
      const r = await hook({ tool: 'task' })
      assert.strictEqual(r, undefined, 'GLUDD_DEPTH_ENFORCE=0 must bypass enforcement')
    })

    it('T21: enforce=0 at depth=3 allows agent dispatch', async () => {
      const { hook } = await freshPlugin(3, '0')
      const r = await hook({ tool: 'agent' })
      assert.strictEqual(r, undefined, 'GLUDD_DEPTH_ENFORCE=0 must bypass for all dispatch tools')
    })

    it('T22: enforce=1 (explicit) at depth=3 still blocks', async () => {
      const { hook } = await freshPlugin(3, '1')
      const r = await hook({ tool: 'task' })
      assertDeny(r, 'MAX DEPTH EXCEEDED', 'explicit enforce=1 must still block at max depth')
    })
  })

  // ==========================================================================
  // BEHAVIOR 6 — reportAlive side-effect
  // ==========================================================================
  describe('BEHAVIOR 6: reportAlive side-effect', () => {
    it('T23: calling hook at depth=0 writes ALIVE_PATH with enforce-depth entry', async () => {
      wipeAlive()
      const { hook } = await freshPlugin(0)
      await hook({ tool: 'task' })

      assert.ok(fs.existsSync(ALIVE_PATH), 'ALIVE_PATH must exist after hook call')
      const alive = JSON.parse(fs.readFileSync(ALIVE_PATH, 'utf8'))
      assert.ok(alive['enforce-depth'], 'alive record must contain enforce-depth entry')
      assert.strictEqual(typeof alive['enforce-depth'].last_seen, 'number', 'last_seen must be a timestamp')
      assert.strictEqual(typeof alive['enforce-depth'].ts, 'number', 'ts must be a timestamp')
      assert.strictEqual(typeof alive['enforce-depth'].loaded, 'number', 'loaded must be a timestamp')
      assert.ok(alive['enforce-depth'].ts > 0, 'timestamp must be positive')
    })

    it('T24: calling hook at depth=3 (denied path) still writes ALIVE_PATH', async () => {
      wipeAlive()
      const { hook } = await freshPlugin(3)
      await hook({ tool: 'task' })

      assert.ok(fs.existsSync(ALIVE_PATH), 'ALIVE_PATH must exist even on denied dispatch')
      const alive = JSON.parse(fs.readFileSync(ALIVE_PATH, 'utf8'))
      assert.ok(alive['enforce-depth'], 'alive record must contain enforce-depth entry on deny path')
      assert.ok(alive['enforce-depth'].ts > 0, 'timestamp must be positive on deny path')
    })

    it('T25: calling hook with non-dispatch tool also writes ALIVE_PATH', async () => {
      wipeAlive()
      const { hook } = await freshPlugin(0)
      await hook({ tool: 'read' })

      assert.ok(fs.existsSync(ALIVE_PATH), 'ALIVE_PATH must exist after read-tool hook call')
      const alive = JSON.parse(fs.readFileSync(ALIVE_PATH, 'utf8'))
      assert.ok(alive['enforce-depth'], 'alive record must contain enforce-depth entry for read calls')
    })

    it('T26: loaded timestamp is stable across multiple calls', async () => {
      wipeAlive()
      const { hook } = await freshPlugin(0)
      await hook({ tool: 'task' })
      const alive1 = JSON.parse(fs.readFileSync(ALIVE_PATH, 'utf8'))
      const loaded1 = alive1['enforce-depth'].loaded

      // Small delay to ensure timestamp differs if loaded was rewritten
      await new Promise(r => setTimeout(r, 50))
      await hook({ tool: 'task' })
      const alive2 = JSON.parse(fs.readFileSync(ALIVE_PATH, 'utf8'))
      const loaded2 = alive2['enforce-depth'].loaded

      assert.strictEqual(loaded2, loaded1, 'loaded timestamp must be stable (set once, not overwritten)')
      assert.ok(alive2['enforce-depth'].ts > alive1['enforce-depth'].ts,
        'ts must advance on second call')
    })
  })
})
