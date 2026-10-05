# Enforcement Test Quality Guide

> **Audience**: agents writing or reviewing enforcement plugin tests.
> **Last updated**: 2026-07-13.
> **Source of truth**: `scripts/test_hook_runtime.py` and the 28 test files under `tests/unit/test_*plugin*.py`.
> **Policy anchor**: AGENTS.md "Self-Test Quality — Structural vs Behavioral" section.

---

## 1. Why Structural Tests Are Not Real Tests

A test that greps source code for constants is a **documentation test**, not a
behavioral test. It proves intent (what the author *wanted*), not behavior (what
the code *does at runtime*).

### 1.1 The "FLOOR=7" counterexample

```python
# tests/unit/test_enforcement_floor_plugin.py — structural test (L50-55)
def test_floor_default_is_7(self):
    src = _src()
    m = re.search(r'CLAUDE_AGENT_FLOOR",\s*"(\d+)"', src)
    assert m, "CLAUDE_AGENT_FLOOR default not found"
    assert m.group(1) == "7"
```

This test passes if the string `"7"` appears in the source file. It passes
when:
- The constant is correct but the hook's enforcement logic is buggy.
- A hot-reloaded module overrides `FLOOR` to a different value.
- `process.env.CLAUDE_AGENT_FLOOR` is set to a different value at runtime.
- The hook is never invoked because it's missing from the plugin's return object.
- A TypeScript compilation error silently means the code never runs.

**A source-string grep proves nothing about runtime behavior.** The project
discovered this empirically: 800+ structural tests passed while enforcement
failed at runtime. Python re-implementations of TypeScript state machines (e.g.,
`test_verified_claims_plugin.py` re-implements `shouldBlock()`) were internally
consistent but shadowed the real logic — when TS diverged from the Python model,
no test caught it.

### 1.2 The 3 failure modes structural tests cannot catch

| Failure mode | Structural test result | Actual behavior |
|---|---|---|
| TS code has a runtime bug (bad regex, wrong return type) | PASS (string is present) | Hook silently returns wrong decision |
| Hot-reloaded module overrides compiled-in constant | PASS (source file unchanged) | Runtime behavior changed |
| Plugin throws uncaught exception → crashes the editor | PASS (source code looks correct) | Editor wedged |
| Env var `GLUDD_FLOOR_ENFORCE=0` should disable but doesn't | PASS (constant present) | Enforcement fires despite disable intent |
| `OPENCODE_SUBAGENT=1` guard is missing from one hook | PASS (guard exists in other hooks) | Subagent deadlocked |

### 1.3 Structural tests are NOT useless

They serve a valuable supplementary role:
- **Documentation**: prove the plugin ships with the right constants.
- **Config integrity**: verify `opencode.json` registration.
- **Guard existence**: confirm subagent guards and catch blocks are present.
- **Pattern enforcement**: detect copy-paste errors (hook A has guard, hook B doesn't).

But a structural test alone is insufficient. Every plugin needs at least one
**runtime test** that invokes the actual hook function and asserts on what it
returns.

---

## 2. The 4-Part Hook Lifecycle Test Template

Every enforcement plugin hook must be tested across these four scenarios.
Missing any one is a test gap.

### 2.1 Part 1: Normal operation — violation triggers enforcement

The hook receives a violating input and returns the expected block.

```python
# scripts/test_hook_runtime.py — test_clean_tree_dirty_dispatch_blocked() (L1454)
def test_clean_tree_dirty_dispatch_blocked():
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_runtime.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test dirty file for runtime hook test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code)
        assert result is not None, "Expected deny object, got None"
        assert result.get("permissionDecision") == "deny"
        assert "DIRTY TREE" in result.get("message", "")
    finally:
        try: os.unlink(test_file)
        except OSError: pass
```

Key pattern: set up the violating state → call the hook → assert on the
return value's `permissionDecision` and `message`.

### 2.2 Part 2: Env-var disable — enforcement skipped

The env-var escape hatch (`GLUDD_*_ENFORCE=0`) must actually disable the hook.

```python
# scripts/test_hook_runtime.py — test_clean_tree_env_disabled() (L1479)
def test_clean_tree_env_disabled():
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_disabled.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try: os.unlink(test_file)
        except OSError: pass
```

Key pattern: identical violating input → but with `env_override` setting the
disable env var → assert the hook allows it.

### 2.3 Part 3: Subagent guard — enforcement skipped for subagents

Every hook must check `OPENCODE_SUBAGENT=1` at the top and skip when true.

```python
# scripts/test_hook_runtime.py — test_clean_tree_subagent_guard() (L1502)
def test_clean_tree_subagent_guard():
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_subagent.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try: os.unlink(test_file)
        except OSError: pass
```

Key pattern: identical violating input + `OPENCODE_SUBAGENT=1` → hook allows.

### 2.4 Part 4: Fail-open — corrupt state does not crash

A broken state file must silently return to allow, never throw uncaught.

```python
# scripts/test_hook_runtime.py — test_deadline_corrupt_state_fail_open() (L774)
def test_deadline_corrupt_state_fail_open():
    stale_state = os.path.join("/tmp", f"test-deadlines-corr-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_TASK_DEADLINE_STATE": stale_state})
    assert result is None or result.get("allowed") == True
    _clean_state_files(stale_state)
```

Key pattern: write corrupt state → call hook → assert it doesn't crash and
allows the tool call (or returns the original output for `text.complete` hooks).

### 2.5 The template

```python
def test_<plugin>_<scenario>():
    """<Scenario description>."""
    # -------- SETUP: Create violating state --------
    # e.g., write a dirty file, set a stale deadline, pre-populate a state file

    # -------- EXECUTE: Call the actual hook --------
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-<name>.ts')
const plugin = await mod.default({{}})
const result = await plugin['<hook>']({{<input>}}, <output>)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={...})

    # -------- ASSERT: Check the return value --------
    # Part 1 (normal): assert result.get("permissionDecision") == "deny"
    # Part 2 (env disable): assert result is None or allowed
    # Part 3 (subagent): assert result is None or allowed
    # Part 4 (fail-open): assert result is None or allowed (no crash)

    # -------- CLEANUP: Remove state files --------
    _clean_state_files(...)
```

---

## 3. How `test_hook_runtime.py` Works

### 3.1 Architecture

The runtime test harness (`scripts/test_hook_runtime.py`, 2009 lines) is a
Python file that spawns Node.js subprocesses to execute actual TypeScript plugin
code. It is NOT a TypeScript-to-Python reimplementation — it runs the real code.

```text
Python test → writes TS snippet to /tmp/hook_test_<N>.ts
            → spawns: node --experimental-strip-types /tmp/hook_test_<N>.ts
            → TS code imports the real plugin, calls a hook
            → hook result (or thrown error) is JSON.stringify'd to stdout
            → Python reads stdout, parses JSON, asserts
```

### 3.2 Core helpers

**`_run_ts(ts_code, env_override, timeout)`** (L35):
Writes the TS snippet to a temp file, runs it via Node, captures stdout as
parsed JSON. Returns `None` if stdout is empty (hook returned undefined/void).
Raises `AssertionError` if Node exits non-zero.

**`_factory_plugin_code(plugin_rel_path, hook_name, call_code)`** (L82):
Generates TS for async-factory plugins (`export default (async ({}) => {...})`).
Loads the module, calls the factory, then calls the hook.

**`_pluginapi_code(plugin_rel_path, call_code)`** (L96):
Generates TS for PluginAPI-style plugins
(`export default function plugin(api: PluginAPI): void`). Creates a mock API
object, registers the hook, then calls the registered callback.

**`_clean_state_files(*paths)`** (L112):
Removes state files before/after tests to prevent cross-test contamination.

### 3.3 What the harness can test

| Capability | Pattern | Example |
|---|---|---|
| Invoke `tool.execute.before` | `await plugin['tool.execute.before'] (input, output)` | `test_floor_streak_max_plus_one_denied` |
| Invoke `text.complete` | `await plugin['experimental.text.complete'] (undefined, output)` | `test_floor_text_complete_blocks_on_zero_dispatches` |
| Invoke `session.idle` | `await plugin['session.idle']()` | Internal state resets |
| Call exported pure functions | `mod.functionName(args)` | `test_clean_tree_count_dirty_files_nonzero` |
| Handle thrown Errors | `try/catch` in TS, log `{permissionDecision: "deny"}` | `test_deletion_over_threshold_blocked` |
| Read/write state files from TS | `import fs from 'node:fs'` in code | `test_enhancement_wave_80pct_fixes_triggers_text_complete_block` |

### 3.4 What the harness CANNOT test

- Hooks that depend on OS-level side effects (running `git status` on real repo)
  — these are best-effort; the test tolerates dirty trees in a live repo.
- Hooks that call external APIs (GitHub, CI) — mock or skip.
- `system.transform` hooks — the harness tests state file side effects instead.
- Hooks that use `process.chdir()` or manipulate the filesystem beyond `/tmp`.

---

## 4. Test Pattern Examples: Good vs Bad

### 4.1 enforce-floor: floor breach denial

**BAD (structural)**:
```python
def test_block_sends_permission_decision_deny(self):
    src = _src()
    assert 'permissionDecision: "deny"' in src
```
This proves the string exists in source. It does NOT prove the hook returns a
deny when the streak breaches.

**GOOD (runtime)**:
```python
# scripts/test_hook_runtime.py — test_floor_streak_max_plus_one_denied() (L835)
def test_floor_streak_max_plus_one_denied():
    tasks_path = f"/tmp/gludd-test-tasks-floor-{os.getpid()}.md"
    # ... set up pending work + fake session state ...
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)  # streak 0→1
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)  # streak 1→2
const r3 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)  # streak 2→3
console.log(JSON.stringify({{r1: r1 ?? null, r2: r2 ?? null, 'r3_deny': r3?.permissionDecision === 'deny'}}))
"""
    result = _run_ts(code, ...)
    assert result["r1"] is None    # allowed
    assert result["r2"] is None    # allowed (at threshold)
    assert result["r3_deny"] == True  # BLOCKED — this is what we're testing
```
This proves the hook returns `{permissionDecision: "deny"}` when the streak
exceeds `MAX_STREAK` with open work present.

### 4.2 enforce-enhancement-ratio: fix keyword classification

**BAD (structural)**:
```python
def test_fix_keywords_present(self):
    src = _src()
    assert "fix" in src  # does NOT prove classification works
```

**GOOD (runtime)**:
```python
# scripts/test_hook_runtime.py — test_enhancement_fix_keywords_classify_correctly() (L323)
def test_enhancement_fix_keywords_classify_correctly():
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'bug fix for login'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{waveLen: state.wave.length, type: state.wave[0]?.type, sessionFixes: state.session_fixes}}))
"""
    result = _run_ts(code)
    assert result["waveLen"] == 1
    assert result["type"] == "fix"            # classification worked
    assert result["sessionFixes"] == 1         # session counter incremented
```

### 4.3 enforce-deadline: stale task blocking

**BAD (structural)**:
```python
def test_timeout_default(self):
    src = _src()
    assert "300000" in src  # does NOT prove the hook enforces the timeout
```

**GOOD (runtime)**:
```python
# scripts/test_hook_runtime.py — test_deadline_task_over_timeout_blocked() (L689)
def test_deadline_task_over_timeout_blocked():
    stale_state = os.path.join("/tmp", f"test-deadlines-blk-{os.getpid()}.json")
    stale_file = os.path.join("/tmp", f"gludd-task-stale-blk-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        json.dump({"stale-task-1": int(time.time() * 1000) - 400_000}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
        "GLUDD_TASK_STALE_FILE": stale_file,
    })
    assert result is not None, "Expected deny object, got None (allowed)"
    assert result.get("permissionDecision") == "deny"
    assert "DEADLINE EXCEEDED" in result.get("message", "")
    _clean_state_files(stale_state, stale_file)
```

### 4.4 enforce-verified-claims: done-words without evidence

**BAD (structural)**:
```python
def test_done_words_list(self):
    words = _extract_done_words(_plugin_source())
    assert "committed" in words  # does NOT prove the text.complete hook fires
```

**GOOD (runtime)**:
```python
# scripts/test_hook_runtime.py — test_verified_claim_no_evidence_blocked() (L1540)
def test_verified_claim_no_evidence_blocked():
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock('everything committed')}}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] == True, f"Unverified claim should be blocked"
```

---

## 5. The TDD Workflow for Enforcement

### 5.1 Write a failing runtime test FIRST

Before writing any enforcement code, write a runtime test that calls the hook
and asserts the thing you need. It will fail because the plugin doesn't exist
or doesn't handle the case yet.

```text
1. Identify the behavior: "After 3 consecutive non-dispatch calls with open work,
   the hook must return {permissionDecision: 'deny'}."

2. Write test function in scripts/test_hook_runtime.py:
   def test_myplugin_streak_breach_denied():
       # ... set up state: make 3 non-dispatch calls ...
       # ... assert r3["permissionDecision"] == "deny" ...

3. Run: make test-hook-runtime -k myplugin
   → FAILS (plugin doesn't exist or doesn't handle the case)

4. Write the enforcement code in .opencode/plugin/enforce-myplugin.ts

5. Run: make test-hook-runtime -k myplugin
   → PASSES

6. Run full suite: make test-hook-runtime
   → Confirm no regressions

7. Add structural tests in tests/unit/test_myplugin.py
   → Verify plugin registration, constants, guards, catch blocks
```

### 5.2 The test → code → verify flow for existing plugins

When modifying an existing plugin:

```text
1. Find the runtime test for the behavior you're changing.
   If none exists, write one BEFORE touching the plugin code.
2. Run: make test-hook-runtime -k <test_name>
   → Read the failure — it's your spec.
3. Edit the plugin code.
4. Run: make test-hook-runtime -k <test_name>
   → Must pass. If not, the code doesn't match the spec.
5. Run: make test-hook-runtime  (full suite)
   → MUST be green before commit.
6. Run structural tests: make test TESTFILE=tests/unit/test_<plugin>.py
   → Update if the changed behavior invalidated old assertions.
```

### 5.3 The anti-pattern (forbidden)

```text
1. Edit plugin code directly.
2. Run: make test-hook-runtime   → some test fails
3. "Fix" the test to match the new behavior without understanding
   why the old test existed.
4. Commit.
```

Every runtime test encodes a past failure mode. If a test breaks, the question
is "what changed and does the new behavior still prevent the same failure mode?"
— not "how do I silence this test?"

---

## 6. How to Add a Runtime Test to `test_hook_runtime.py`

### 6.1 Step-by-step

**Step 1: Choose the hook surface.**
```python
# tool.execute.before (blocks a tool call):
result = await plugin['tool.execute.before']({tool: '<name>', args: {...}}, undefined)

# text.complete (blocks/mutates generated text):
output = {text: 'original text'}
result = await plugin['experimental.text.complete'](undefined, output)

# Pure exported function:
console.log(JSON.stringify(mod.functionName(args...)))
```

**Step 2: Choose the plugin loading pattern.**

Async-factory plugins (most plugins):
```python
code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-<name>.ts')
const plugin = await mod.default({{}})
const result = await plugin['<hook>'](<input>, <output>)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
```

PluginAPI plugins (enforce-clean-tree, enforce-no-wait, enforce-deletion-gate):
```python
code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-<name>.ts')
mod.default(api)
const result = registeredHook(<input>)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
```

**Step 3: Provide state (env vars, temp files).**

```python
result = _run_ts(code, env_override={
    "GLUDD_<NAME>_ENFORCE": "0",                              # disable test
    "OPENCODE_SUBAGENT": "1",                                   # subagent test
    "GLUDD_<NAME>_STATE": "/tmp/test-state-{os.getpid()}.json", # custom state path
})
```

**Step 4: Assert on the return value.**

```python
# Hook returned undefined (allowed):
assert result is None

# Hook returned {permissionDecision: "deny"}:
assert result.get("permissionDecision") == "deny"
assert "EXPECTED ERROR TEXT" in result.get("message", "")

# Hook returned modified text:
assert result["blocked"] == True
assert "FLOOR BREACH" in result.get("finalText", "")

# Exported function returned a value:
assert result["shouldBlock"] == True
```

**Step 5: Clean up state files (ALWAYS).**

```python
_clean_state_files(state_file, block_file, "/tmp/gludd-block-counter.json")
```

Failing to clean up leaves stale state that breaks subsequent tests.

### 6.2 Complete example: adding a test for enforce-no-suppressions runtime behavior

```python
# scripts/test_hook_runtime.py — new test

def test_no_suppression_pylint_disable_blocked():
    """Text contains '# pylint: disable=E1101' → isSuppressionComment returns true."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-suppressions.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# pylint: disable=E1101'),
    verdict: mod.shouldAllowEdit('src/app.py', '# pylint: disable=E1101'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] == True
    assert result["verdict"]["allow"] == False
    assert "forbidden" in result["verdict"].get("reason", "")
```

### 6.3 Naming convention

Test function names follow the pattern:
```text
test_<plugin_short_name>_<what_is_tested>[_<scenario>]
```

Examples:
- `test_floor_streak_max_plus_one_denied` — enforce-floor, streak threshold denial
- `test_deadline_corrupt_state_fail_open` — enforce-deadline, fail-open on corrupt state
- `test_enhancement_fix_keywords_classify_correctly` — enforce-enhancement-ratio, fix keyword classification
- `test_clean_tree_env_disable` — enforce-clean-tree, env-var disable path

### 6.4 Where to add the test in the file

Add your test in the plugin's section (delimited by `# --- <plugin-name> ---`
comments). If the plugin doesn't have a section yet, add one. The sections are
grouped approximately:

| Lines | Plugin section |
|---|---|
| 134–295 | enforce-clean-tree |
| 297–578 | enforce-enhancement-ratio |
| 581–665 | enforce-delegate |
| 669–803 | enforce-deadline |
| 808–1106 | enforce-floor |
| 1110–1183 | enforce-multitask |
| 1186–1430 | enforce-stop |
| 1433–1522 | enforce-clean-tree (continued) |
| 1525–1623 | enforce-verified-claims + enforce-no-suppressions |
| 1640–1901 | enforce-no-wait + enforce-deletion-gate + enforce-session-start |
| 1904–2000 | enforce-make |

Tests are standard pytest functions. No class wrappers needed.

---

## 7. New Enforcement Plugin Checklist

Before shipping a new plugin, complete EVERY item. A plugin with 0 runtime
tests is dead code — it exists on disk but its behavior is unverified.

### 7.1 Runtime tests (in `scripts/test_hook_runtime.py`)

- [ ] **Normal operation test**: hook returns `{permissionDecision: "deny"}` on violation
- [ ] **Env-var disable test**: `GLUDD_<NAME>_ENFORCE=0` → tool call allowed
- [ ] **Subagent guard test**: `OPENCODE_SUBAGENT=1` → enforcement skipped
- [ ] **Fail-open test**: corrupt state file → no crash, tool call allowed
- [ ] **Run**: `make test-hook-runtime -k <plugin>` → all 4+ tests pass

### 7.2 Structural tests (in `tests/unit/test_<plugin>.py`)

- [ ] **File existence**: plugin `.ts` file exists
- [ ] **opencode.json registration**: plugin referenced in `opencode.json`
- [ ] **Export shape**: exports the expected hooks (`tool.execute.before`, `text.complete`, etc.)
- [ ] **Subagent guard presence**: `OPENCODE_SUBAGENT` check in every hook
- [ ] **Fail-open catch blocks**: all hook functions wrapped in try/catch
- [ ] **Key constants correct**: defaults match spec
- [ ] **Run**: `make test TESTFILE=tests/unit/test_<plugin>.py` → all pass

### 7.3 Gate pipeline

- [ ] **`make test-hook-runtime`**: full harness suite passes (52+ tests)
- [ ] **`make lint`**: zero ruff warnings
- [ ] **`make typecheck`**: zero mypy errors
- [ ] **`make test-count`**: zero collection errors
- [ ] **`make gate`**: full gate green OR `make gate-background` + verified `.gate-status` PASS

### 7.4 Documentation

- [ ] **Plugin table updated**: add row to `docs/ENFORCEMENT_PLUGINS.md` Section 2
- [ ] **State file map updated**: add rows to Section 5 (if new state files)
- [ ] **AGENTS.md table updated**: add row to the enforcement plugin status table (line ~95 of AGENTS.md)
- [ ] **Escape hatch documented**: env var to disable (Section 6 of ENFORCEMENT_PLUGINS.md)

### 7.5 Common mistakes to avoid

| Mistake | Why it's wrong | Fix |
|---|---|---|
| "I tested it manually by running opencode" | Not reproducible, not in gate pipeline | Write a runtime test |
| "The structural tests pass so it works" | Source-code assertions ≠ runtime behavior | Add `test_hook_runtime.py` test |
| "I only wrote the normal-operation test" | Missing the 3 other lifecycle tests | Add env-disable, subagent, fail-open |
| "The hook throws, which is fine since it's a block" | Must be caught; uncaught throw = crashed editor | `try/catch` → `{permissionDecision: "deny"}` |
| "I'll add tests next session" | Tests are part of the feature, not a follow-up | Write tests in the SAME session |
| "State file cleanup isn't necessary" | Stale state breaks subsequent tests | Always call `_clean_state_files(...)` |

---

## 8. Running the Tests

```bash
# Full harness suite
make test-hook-runtime

# Run one plugin's tests
make test-hook-runtime -k floor

# Run one specific test
make test-hook-runtime -k streak_max_plus_one

# Run full harness + all unit tests
make test TESTFILE=tests/unit/test_*plugin*.py
make test-hook-runtime

# Check test count (52+ expected)
make test-hook-runtime 2>&1 | grep "passed"
```

The `make test-hook-runtime` target (Makefile L734) runs:
```text
uv run python scripts/test_hook_runtime.py -v
```

It is invoked as part of `make gate` (L445) and `make gate-lite` (L535).
Nothing that breaks this target can pass the gate.

---

## 9. Reference: Test Coverage by Plugin

| Plugin | Runtime tests | Structural test file | Total tests |
|---|---|---|---|
| enforce-floor | 16 | `test_enforcement_floor_plugin.py` (836 lines, ~90 assertions) | ~106 |
| enforce-deadline | 9 | `test_enforcement_deadline_plugin.py` (860 lines) | ~80 |
| enforce-enhancement-ratio | 12 | `test_enhancement_ratio_plugin.py` | ~60 |
| enforce-clean-tree | 11 | `test_clean_tree_plugin.py` | ~40 |
| enforce-delegate | 4 | `test_enforcement_delegate_plugin.py` | ~35 |
| enforce-multitask | 4 | `test_multitask_plugin.py` | ~25 |
| enforce-stop | 8 | `test_todo_guard_plugin.py` + `test_false_done_plugin.py` | ~45 |
| enforce-verified-claims | 4 | `test_verified_claims_plugin.py` (267 lines) | ~27 |
| enforce-no-suppressions | 4 | `test_no_suppression_comments_plugin.py` | ~20 |
| enforce-no-wait | 3 | `test_no_wait_plugin.py` | ~15 |
| enforce-deletion-gate | 3 | `test_enforcement_deletion_gate_plugin.py` | ~12 |
| enforce-session-start | 5 | `test_session_start_plugin.py` + `test_enforcement_session_start_plugin.py` | ~40 |
| enforce-make | 11 | `test_enforce_make_plugin.py` | ~30 |
| enforce-commit-lock | 0 (worktree isolation required) | `test_commit_lock_plugin.py` | ~20 |
| **TOTAL** | **94 runtime** | **28 structural files** | **~555** |

Plugin-specific counts are approximate; run `make test-count` for the current
exact count. The structural tests complement but do not replace the 94 runtime
tests — each runtime test proves behavior, not just source-code shape.
