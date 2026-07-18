// enforce-tdd.test.node.mjs — behavioral runtime tests for the TDD plugin.
//
// Verifies the REAL hook behavior by compiling the plugin with esbuild and
// invoking tool.execute.before with constructed arguments against a temp
// filesystem. This is the "Self-Test Quality" requirement: structural tests
// are insufficient — at least one test per plugin MUST invoke the actual
// hook function and assert on the return value.
//
// Behaviors verified:
//   T1  default factory returns tool.execute.before hook
//   T2  exports shouldAllowEdit + candidateTestPaths named helpers
//   T3  DENY write to src/ file when no test file exists
//   T4  DENY edit (not just write) to src/ file when no test exists
//   T5  ALLOW write to src/ file when test file EXISTS
//   T6  ALLOW write to allowlisted __init__.py (no test needed)
//   T7  ALLOW write to *.pyi type stub
//   T8  ALLOW write to tests/ files themselves (writing the test!)
//   T9  ALLOW write to non-src files (docs, configs)
//   T10 Subagent guard: OPENCODE_SUBAGENT=1 bypasses
//   T11 GLUDD_TDD_ENFORCE=0 bypasses
//   T12 Fail-open: garbage input does not throw
//   T13 deny message includes "test FIRST" + candidate path
//   T14 candidateTestPaths matches check_tdd_compliance.py logic
//
// Runner: node --test .opencode/plugin/enforce-tdd.test.node.mjs

import { describe, it, after, before, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import * as fs from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'
import { execSync } from 'node:child_process'

const PROJECT_ROOT = process.cwd()
const OUTFILE = '/tmp/gludd-test-enforce-tdd.js'
const ALIVE_PATH = '/tmp/gludd-plugin-alive.json'

// Park any hot module that would shadow the code under test.
const HOT_PATH = '/tmp/gludd-hot-enforce-tdd.js'
const HOT_BACKUP = '/tmp/gludd-hot-enforce-tdd.js.test-backup'
if (fs.existsSync(HOT_PATH)) {
  try { fs.renameSync(HOT_PATH, HOT_BACKUP) } catch {}
}

// Environment hardening — start from a clean state.
delete process.env.OPENCODE_SUBAGENT
delete process.env.GLUDD_TDD_ENFORCE
process.env.GLUDD_ALIVE_PATH = ALIVE_PATH

function compileWithEsbuild(outfile) {
  const env = { ...process.env, npm_config_userconfig: '/dev/null' }
  const args = `.opencode/plugin/enforce-tdd.ts --bundle --platform=node --target=node18 --format=cjs --outfile=${outfile}`

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
  TMP_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'gludd-tdd-test-'))
  fs.mkdirSync(path.join(TMP_ROOT, 'src', 'general_ludd'), { recursive: true })
  fs.mkdirSync(path.join(TMP_ROOT, 'src', 'general_ludd', 'sub'), { recursive: true })
  fs.mkdirSync(path.join(TMP_ROOT, 'tests', 'unit'), { recursive: true })
}

function rmTempProject() {
  try { fs.rmSync(TMP_ROOT, { recursive: true, force: true }) } catch {}
}

async function freshPlugin(opts = {}) {
  delete process.env.OPENCODE_SUBAGENT
  if (opts.enforce !== undefined) {
    process.env.GLUDD_TDD_ENFORCE = opts.enforce
  } else {
    delete process.env.GLUDD_TDD_ENFORCE
  }
  if (opts.subagent) process.env.OPENCODE_SUBAGENT = '1'
  // Force the project root so candidateTestPaths resolves under TMP_ROOT.
  process.env.GLUDD_PROJECT_ROOT = TMP_ROOT
  // Clear the module cache + project-root cache so env changes take effect.
  delete _require.cache[_require.resolve(OUTFILE)]
  const m = _require(OUTFILE)
  if (m.invalidateProjectRootCache) m.invalidateProjectRootCache()
  const instance = await m.default({})
  return { m, hook: instance['tool.execute.before'] }
}

function assertDeny(r, needle, msg) {
  assert.ok(r !== null && r !== undefined, msg + ' (got allow instead of deny)')
  assert.strictEqual(r.permissionDecision, 'deny', msg)
  if (needle) {
    assert.ok(r.message.includes(needle),
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
  rmTempProject()
  if (fs.existsSync(HOT_BACKUP)) {
    try { fs.renameSync(HOT_BACKUP, HOT_PATH) } catch {}
  }
}

describe('enforce-tdd', { concurrency: 1 }, () => {

  before(() => { makeTempProject() })
  after(() => { cleanup() })
  afterEach(() => {
    // Remove any test files created during a test so the next test starts clean.
    try {
      for (const f of fs.readdirSync(path.join(TMP_ROOT, 'tests', 'unit'))) {
        fs.rmSync(path.join(TMP_ROOT, 'tests', 'unit', f), { force: true })
      }
    } catch {}
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

    it('T2: exports shouldAllowEdit + candidateTestPaths', () => {
      assert.strictEqual(typeof mod.shouldAllowEdit, 'function',
        'shouldAllowEdit must be a named export for test pinning')
      assert.strictEqual(typeof mod.candidateTestPaths, 'function',
        'candidateTestPaths must be a named export')
      assert.strictEqual(typeof mod.isAllowlisted, 'function')
      assert.strictEqual(typeof mod.isImplementationFile, 'function')
      assert.ok(Array.isArray(mod.ALLOWLIST_PATTERNS))
      assert.ok(mod.ALLOWLIST_PATTERNS.length >= 5,
        `expected >=5 allowlist patterns, got ${mod.ALLOWLIST_PATTERNS.length}`)
    })
  })

  // ==========================================================================
  // T3-T4: DENY — the core TDD rule
  // ==========================================================================
  describe('DENY: src/ edit with no test file', () => {
    it('T3: DENY write to src/general_ludd/foo.py when no test exists', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/foo.py'),
                  content: 'x = 1\n' } },
      )
      assertDeny(r, 'test FIRST', 'write to src/ with no test must be denied')
      assert.ok(r.message.includes('test_general_ludd_foo.py'),
        `deny message must include the candidate test path, got: ${r.message}`)
    })

    it('T4: DENY edit to src/general_ludd/foo.py when no test exists', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'edit' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/foo.py'),
                  newString: 'x = 2' } },
      )
      assertDeny(r, 'TDD VIOLATION', 'edit to src/ with no test must be denied')
    })

    it('T4b: DENY write to nested src/general_ludd/sub/bar.py', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/sub/bar.py'),
                  content: 'y = 2\n' } },
      )
      assertDeny(r, 'test FIRST', 'nested src/ file with no test must be denied')
    })
  })

  // ==========================================================================
  // T5: ALLOW — once the test exists, implementation work is unblocked
  // ==========================================================================
  describe('ALLOW: src/ edit once test file exists', () => {
    it('T5: ALLOW write to src/ when test_general_ludd_foo.py exists', async () => {
      // Create the test file FIRST (the TDD workflow).
      fs.writeFileSync(
        path.join(TMP_ROOT, 'tests/unit/test_general_ludd_foo.py'),
        'from general_ludd.foo import *\ndef test_foo():\n    pass\n',
      )
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/foo.py'),
                  content: 'x = 1\n' } },
      )
      assertAllow(r, 'write to src/ must be allowed once test file exists')
    })

    it('T5b: ALLOW when only the leaf-name test exists (test_foo.py)', async () => {
      fs.writeFileSync(
        path.join(TMP_ROOT, 'tests/unit/test_foo.py'),
        'def test_foo():\n    pass\n',
      )
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'edit' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/foo.py'),
                  newString: 'x = 1' } },
      )
      assertAllow(r, 'leaf-name test candidate must also satisfy the gate')
    })
  })

  // ==========================================================================
  // T6-T7: ALLOWLIST — type defs and package markers don't need tests
  // ==========================================================================
  describe('ALLOWLIST', () => {
    it('T6: ALLOW write to __init__.py (no test required)', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/__init__.py'),
                  content: '"""package."""\n' } },
      )
      assertAllow(r, '__init__.py must be allowlisted')
    })

    it('T7: ALLOW write to types.py / protocols.py / *.pyi', async () => {
      const { hook } = await freshPlugin()
      for (const name of ['typing.py', 'type_defs.py', 'protocols.py', '_types.py', 'foo.pyi']) {
        const r = await hook(
          { tool: 'write' },
          { args: { filePath: path.join(TMP_ROOT, `src/general_ludd/${name}`),
                    content: 'x = 1\n' } },
        )
        assertAllow(r, `${name} must be allowlisted (type def / package marker)`)
      }
    })
  })

  // ==========================================================================
  // T8-T9: scope — tests/ and non-src paths pass through freely
  // ==========================================================================
  describe('SCOPE', () => {
    it('T8: ALLOW write to tests/ files (you are writing the test)', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'tests/unit/test_brand_new.py'),
                  content: 'def test_new():\n    pass\n' } },
      )
      assertAllow(r, 'writing a test file must never be blocked')
    })

    it('T9: ALLOW write to non-src files (docs, configs, scripts)', async () => {
      const { hook } = await freshPlugin()
      for (const p of ['README.md', 'docs/guide.md', 'config/app.yml',
                        'scripts/build.py', 'Makefile']) {
        const r = await hook(
          { tool: 'write' },
          { args: { filePath: path.join(TMP_ROOT, p), content: 'foo\n' } },
        )
        assertAllow(r, `${p} is outside src/general_ludd/ — not in scope`)
      }
    })
  })

  // ==========================================================================
  // T10-T11: bypass switches
  // ==========================================================================
  describe('BYPASS', () => {
    it('T10: subagent guard — OPENCODE_SUBAGENT=1 bypasses', async () => {
      const { hook } = await freshPlugin({ subagent: true })
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/untested.py'),
                  content: 'x = 1\n' } },
      )
      assertAllow(r, 'subagents inherit orchestrator enforcement, never their own')
    })

    it('T11: GLUDD_TDD_ENFORCE=0 bypasses', async () => {
      const { hook } = await freshPlugin({ enforce: '0' })
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/untested.py'),
                  content: 'x = 1\n' } },
      )
      assertAllow(r, 'GLUDD_TDD_ENFORCE=0 is the documented escape hatch')
    })
  })

  // ==========================================================================
  // T12: fail-open
  // ==========================================================================
  describe('FAIL-OPEN', () => {
    it('T12: garbage input does not throw and does not deny', async () => {
      const { hook } = await freshPlugin()
      // No args at all.
      const r1 = await hook({ tool: 'write' }, undefined)
      assertAllow(r1, 'missing output/args must not wedge the editor')
      // Empty args.
      const r2 = await hook({ tool: 'write' }, { args: {} })
      assertAllow(r2, 'empty args must not wedge the editor')
      // Non-write/edit tool.
      const r3 = await hook({ tool: 'read' },
        { args: { filePath: '/tmp/whatever' } })
      assertAllow(r3, 'read tool must pass through (not in scope)')
    })
  })

  // ==========================================================================
  // T13: deny message contract
  // ==========================================================================
  describe('DENY MESSAGE', () => {
    it('T13: deny message includes "test FIRST", "AGENTS.md", and candidate paths', async () => {
      const { hook } = await freshPlugin()
      const r = await hook(
        { tool: 'write' },
        { args: { filePath: path.join(TMP_ROOT, 'src/general_ludd/widget.py'),
                  content: 'pass\n' } },
      )
      assertDeny(r, 'test FIRST')
      assert.ok(r.message.includes('AGENTS.md'),
        `deny must reference AGENTS.md, got: ${r.message}`)
      assert.ok(r.message.includes('test_general_ludd_widget.py'),
        `deny must include candidate test path, got: ${r.message}`)
    })
  })

  // ==========================================================================
  // T14: candidateTestPaths parity with check_tdd_compliance.py
  // ==========================================================================
  describe('CANDIDATE PATH PARITY', () => {
    it('T14: candidateTestPaths matches check_tdd_compliance.py logic', () => {
      // src/general_ludd/daemon.py → two candidates
      const c1 = mod.candidateTestPaths(
        path.join(TMP_ROOT, 'src/general_ludd/daemon.py'), TMP_ROOT)
      assert.ok(c1.length >= 2, `expected >=2 candidates, got ${c1.length}`)
      assert.ok(c1[0].endsWith('test_general_ludd_daemon.py'),
        `full-stem candidate wrong: ${c1[0]}`)
      assert.ok(c1[1].endsWith('test_daemon.py'),
        `leaf candidate wrong: ${c1[1]}`)

      // nested module src/general_ludd/foo/bar.py
      const c2 = mod.candidateTestPaths(
        path.join(TMP_ROOT, 'src/general_ludd/foo/bar.py'), TMP_ROOT)
      assert.ok(c2.some(c => c.endsWith('test_general_ludd_foo_bar.py')),
        `nested full-stem wrong: ${JSON.stringify(c2)}`)
      assert.ok(c2.some(c => c.endsWith('test_bar.py')),
        `nested leaf wrong: ${JSON.stringify(c2)}`)
    })
  })
})
