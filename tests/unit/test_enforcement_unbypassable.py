"""Unbypassable-enforcement behavioral tests (TDD, failing-first).

Verifies that the behavioral enforcement in enforce-stop.ts and
enforce-multitask.ts can NEVER be bypassed — not via disable env vars, not
via disengage files, and not via unit-mismatch blind spots in the CI cache.

Pattern (from .opencode/plugin/enforce-multitask.test.node.mjs): the actual
TypeScript plugin is compiled with esbuild (--bundle --format=cjs), loaded in
a fresh node process via createRequire, and the REAL hook functions are
invoked with constructed arguments. The hot-module delegation path
(/tmp/gludd-hot-*.js) is neutralized inside the bundle so the compiled-in
defaultImpl — the current source — is what runs, hermetically.

The four pins (task spec):

  1. text.complete blanks "Done. All work complete." with pending work even
     when GLUDD_STOP_ENFORCE=0 is set. The env var (documented at
     enforce-stop.ts:37 as "disables ALL enforcement") must never bypass the
     text-only block.
  2. text.complete blanks the same text even when EVERY disengage mechanism
     is armed (watchdog disengage file with valid disengage_until AND the
     block-counter disengageUntil). Disengage may only skip heuristics,
     never the fundamental hasRealPendingWork() block (2026-07-15 fix).
  3. text.complete blanks a summary when the watchdog CI cache reports a
     CI failure. agent_watchdog.py writes last_ci_check via time.time()
     (epoch SECONDS); enforce-stop.ts compares it against Date.now()
     (MILLISECONDS) with a 600_000ms window, so the watchdog-format cache
     is ALWAYS treated as stale and CI RED is invisible. The fixed plugin
     must normalize seconds/ms so the signal registers.
  4. enforce-multitask denies edit with zero dispatches and pending work
     when the operator opts into a mandatory minimum via
     GLUDD_MIN_DISPATCHES / GLUDD_MULTITASK_MIN_DISPATCHES, and bypasses
     when GLUDD_MULTITASK_FLOOR_ENFORCE=0. The minimum is opt-in: absent
     min-dispatch env vars, no mandatory minimum is active.

Expected status against CURRENT code (documented, not aspirational):
  test 1 — PASSES (regression pin: the env var is inert today; this test
           prevents a future "fix" from wiring it into the text-only block)
  test 2 — PASSES (regression pin of the 2026-07-15 disengage narrowing)
  test 3 — FAILS  (seconds-vs-ms bug: ciVerdictPendingOrRed never true for
           watchdog-format caches)
  test 4 — PASSES (operator opt-in minimum: min-dispatch env activates the
           under-floor block; FLOOR_ENFORCE=0 disables it)
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
NODE = os.environ.get("GLUDD_NODE_BIN", "node")

if shutil.which(NODE) is None:
    pytest.skip("node binary unavailable", allow_module_level=True)

_BUNDLE_CACHE: dict[str, Path] = {}

_AMBIENT_ENV_KEYS = [
    "OPENCODE_SUBAGENT",
    "GLUDD_STOP_ENFORCE",
    "GLUDD_MULTITASK_FLOOR_ENFORCE",
    "GLUDD_MIN_DISPATCHES",
    "GLUDD_MULTITASK_MIN_DISPATCHES",
    "GLUDD_MULTITASK_MAX_DISPATCHES",
    "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD",
    "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS",
    "GLUDD_MSG_GAP_MS",
    "GLUDD_DISENGAGE_PATH",
    "GLUDD_PROJECT_ROOT",
    "GLUDD_TASKS_MD",
    "GLUDD_BLOCK_COUNTER_FILE",
    "GLUDD_STOP_STATE_FILE",
    "GLUDD_PERSIST_STOP_BLOCK_FILE",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT",
    "GLUDD_STOP_TOOL_COUNTS_FILE",
    "GLUDD_WATCHDOG_CI_FILE",
    "GLUDD_MULTITASK_STATE_FILE",
    "GLUDD_ALIVE_PATH",
    "GLUDD_STREAK_FILE",
]


def _esbuild_bundle(plugin_name: str) -> Path:
    """Compile .opencode/plugin/<name>.ts to a CJS bundle (cached per process).

    After bundling, the hot-module path prefix is rewritten to a nonexistent
    prefix so loadHotModule() finds nothing and always returns the compiled-in
    defaultImpl — the test exercises the CURRENT source, not a stale
    /tmp/gludd-hot-*.js artifact.
    """
    cached = _BUNDLE_CACHE.get(plugin_name)
    if cached is not None and cached.exists():
        return cached

    src = PLUGIN_DIR / f"{plugin_name}.ts"
    assert src.exists(), f"plugin source missing: {src}"
    out = Path(f"/tmp/gludd-test-unbypass-{plugin_name}-{os.getpid()}.js")
    args = [
        f".opencode/plugin/{plugin_name}.ts",
        "--bundle",
        "--platform=node",
        "--target=node18",
        "--format=cjs",
        f"--outfile={out}",
    ]
    attempts = [
        [str(ROOT / ".opencode" / "node_modules" / ".bin" / "esbuild"), *args],
        ["esbuild", *args],
        ["npx", "--yes", "esbuild", *args],
    ]
    compiled = False
    errors: list[str] = []
    for cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{cmd[0]}: {exc}")
            continue
        if proc.returncode == 0 and out.exists():
            compiled = True
            break
        errors.append(f"{cmd[0]}: rc={proc.returncode} {proc.stderr[:200]}")
    if not compiled:
        pytest.skip(f"esbuild unavailable/failed: {errors}")
    bundle_src = out.read_text()
    bundle_src = re.sub(
        r"var (import_meta\d*) = \{\};",
        r'var \1 = { url: require("node:url").pathToFileURL(__filename).href };',
        bundle_src,
    )
    bundle_src = bundle_src.replace(
        "/tmp/gludd-hot-",
        f"/tmp/gludd-hot-unbypass-absent-{os.getpid()}-",
    )
    out.write_text(bundle_src)
    _BUNDLE_CACHE[plugin_name] = out
    return out


def _run_driver(script: str, env_overrides: dict[str, str], cwd: str) -> dict:
    """Run a .mjs driver in a fresh node process; return the last JSON line."""
    env = os.environ.copy()
    for key in _AMBIENT_ENV_KEYS:
        env.pop(key, None)
    env["OPENCODE_SUBAGENT"] = ""
    env.update(env_overrides)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".mjs",
        prefix="gludd-unbypass-driver-",
        dir="/tmp",
        delete=False,
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        proc = subprocess.run(
            [NODE, script_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
            env=env,
        )
        assert proc.returncode == 0, (
            f"driver exited {proc.returncode}\nstderr: {proc.stderr[:1000]}\nstdout: {proc.stdout[:500]}"
        )
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            assert "driverError" not in parsed, f"driver error: {parsed}"
            return parsed
        raise AssertionError(
            f"driver produced no JSON output\nstdout: {proc.stdout[:800]}\nstderr: {proc.stderr[:800]}"
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(script_path)


def _mk_workdir(unchecked_tasks: bool) -> Path:
    """Temp project root + state dir under /tmp/gludd-* (workspace policy)."""
    base = Path(tempfile.mkdtemp(prefix="gludd-unbypass-", dir="/tmp"))
    project = base / "project"
    project.mkdir()
    if unchecked_tasks:
        (project / "TASKS.md").write_text(
            "# Test Tasks\n\n- [ ] unbypassable enforcement work item\n",
            encoding="utf-8",
        )
    else:
        (project / "TASKS.md").write_text(
            "# Test Tasks\n\n- [x] everything previously finished\n",
            encoding="utf-8",
        )
    (base / "state").mkdir()
    return base


def _stop_env(base: Path) -> dict[str, str]:
    """Redirect every env-overridable enforce-stop state path into the temp
    dir so live-session state cannot leak in (and vice versa)."""
    state = base / "state"
    return {
        "GLUDD_PROJECT_ROOT": str(base / "project"),
        "GLUDD_DISENGAGE_PATH": str(state / "disengage.json"),
        "GLUDD_BLOCK_COUNTER_FILE": str(state / "block-counter.json"),
        "GLUDD_STOP_STATE_FILE": str(state / "stop-state.json"),
        "GLUDD_PERSIST_STOP_BLOCK_FILE": str(state / "persist-block.json"),
        "GLUDD_STOP_TEXT_COMPLETE_COUNT": str(state / "tc-count.json"),
        "GLUDD_STOP_TOOL_COUNTS_FILE": str(state / "tool-counts.json"),
        "GLUDD_ALIVE_PATH": str(state / "alive.json"),
        "GLUDD_STREAK_FILE": str(state / "streak.json"),
        "GLUDD_WATCHDOG_CI_FILE": str(state / "watchdog-ci.json"),
    }


def test_stop_env_isolates_ci_cache_path(tmp_path: Path) -> None:
    """CI cache state must be per-test so xdist shards cannot race globally."""
    env = _stop_env(tmp_path)
    assert Path(env["GLUDD_WATCHDOG_CI_FILE"]).is_relative_to(tmp_path)


def _text_complete_driver(bundle: Path, text: str, pre_js: str = "") -> str:
    return f"""
import {{ createRequire }} from "node:module";
import * as fs from "node:fs";
const require_ = createRequire(import.meta.url);
{pre_js}
const mod = require_({json.dumps(str(bundle))});
const plugin = await mod.default({{}});
const hook = plugin["experimental.text.complete"] || plugin["text.complete"];
if (typeof hook !== "function") {{
  console.log(JSON.stringify({{ driverError: "text.complete hook missing", hooks: Object.keys(plugin) }}));
  process.exit(0);
}}
const original = {json.dumps(text)};
const r = await hook({{}}, original);
let finalText;
if (typeof r === "string") finalText = r;
else if (r && typeof r.text === "string") finalText = r.text;
else if (r === undefined || r === null) finalText = original;
else finalText = JSON.stringify(r);
console.log(JSON.stringify({{
  blanked: finalText !== original,
  hasBlockMarker: finalText.includes("\\u26d4") || finalText.toUpperCase().includes("BLOCKED"),
  finalText: String(finalText).slice(0, 500),
}}));
"""


def _multitask_edit_driver(bundle: Path) -> str:
    return f"""
import {{ createRequire }} from "node:module";
const require_ = createRequire(import.meta.url);
const mod = require_({json.dumps(str(bundle))});
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];
if (typeof hook !== "function") {{
  console.log(JSON.stringify({{ driverError: "tool.execute.before hook missing" }}));
  process.exit(0);
}}
let r;
try {{
  r = await hook({{ tool: "edit" }}, undefined);
}} catch (e) {{
  r = {{ permissionDecision: "deny", message: String((e && e.message) || e) }};
}}
console.log(JSON.stringify({{ result: r === undefined || r === null ? null : r }}));
"""


def _ci_cache_pre_js(ts_expression: str, cache_path: str) -> str:
    """JS that writes the watchdog CI cache exactly as agent_watchdog.py's
    _update_ci_cache() does, immediately before the hook is invoked (minimal
    race window with the live watchdog daemon)."""
    return f"""
fs.writeFileSync({json.dumps(cache_path)}, JSON.stringify({{
  last_ci_check: {ts_expression},
  last_ci_status: "FAILURE",
  last_output: "CI RED: run 29613868503 conclusion='failure'",
}}));
"""


def _assert_blanked(result: dict, context: str) -> None:
    assert result.get("blanked") is True, (
        f"{context}: response was NOT blanked — the original text passed "
        f"through text.complete unchanged. Behavioral enforcement was "
        f"bypassed. Got: {result}"
    )
    assert result.get("hasBlockMarker") is True, (
        f"{context}: blanked replacement must carry a block marker (\u26d4 / 'BLOCKED'). Got: {result}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. text.complete never bypassable via GLUDD_STOP_ENFORCE=0
# ═══════════════════════════════════════════════════════════════════════════


class TestTextCompleteEnvVarUnbypassable:
    """enforce-stop.ts:37 documents 'GLUDD_STOP_ENFORCE=0 disables ALL
    enforcement'. The text-only block with pending work must NEVER honor it."""

    def test_text_complete_blanks_despite_stop_enforce_disabled(self):
        base = _mk_workdir(unchecked_tasks=True)
        try:
            bundle = _esbuild_bundle("enforce-stop")
            env = _stop_env(base)
            env["GLUDD_STOP_ENFORCE"] = "0"
            result = _run_driver(
                _text_complete_driver(bundle, "Done. All work complete."),
                env,
                cwd=str(base / "project"),
            )
            _assert_blanked(
                result,
                "GLUDD_STOP_ENFORCE=0 with unchecked TASKS.md items",
            )
        finally:
            shutil.rmtree(base, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# 2. text.complete never bypassable via disengage
# ═══════════════════════════════════════════════════════════════════════════


class TestTextCompleteDisengageUnbypassable:
    """Both disengage mechanisms armed at once — the watchdog disengage file
    (shared.ts isDisengaged) AND the block-counter disengageUntil (enforce-stop
    local isDisengaged) — must not bypass the pending-work text-only block."""

    def test_text_complete_blanks_despite_valid_disengage(self):
        base = _mk_workdir(unchecked_tasks=True)
        try:
            bundle = _esbuild_bundle("enforce-stop")
            env = _stop_env(base)
            now_ms = int(time.time() * 1000)
            Path(env["GLUDD_DISENGAGE_PATH"]).write_text(
                json.dumps({"disengage_until": now_ms + 240_000}),
                encoding="utf-8",
            )
            Path(env["GLUDD_BLOCK_COUNTER_FILE"]).write_text(
                json.dumps(
                    {
                        "consecutiveBlocks": 0,
                        "totalBlocks": 0,
                        "lastBlockTs": now_ms,
                        "disengageUntil": now_ms + 120_000,
                    }
                ),
                encoding="utf-8",
            )
            result = _run_driver(
                _text_complete_driver(bundle, "Done. All work complete."),
                env,
                cwd=str(base / "project"),
            )
            _assert_blanked(
                result,
                "valid disengage files with unchecked TASKS.md items",
            )
        finally:
            shutil.rmtree(base, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# 3. text.complete blocks when CI is RED (watchdog cache format)
# ═══════════════════════════════════════════════════════════════════════════


class TestTextCompleteBlocksOnCiRed:
    """agent_watchdog.py _update_ci_cache() stamps last_ci_check with
    time.time() — epoch SECONDS. enforce-stop.ts hasRealPendingWork() checks
    `Date.now() - lastCheck < 600_000` — MILLISECONDS. A watchdog-format cache
    is therefore always 'stale' and CI RED never registers. The plugin must
    normalize both units so a fresh FAILURE verdict blanks summaries."""

    @staticmethod
    def _run_ci_leg(ts_expression: str, base: Path, state_name: str) -> tuple[dict, dict]:
        bundle = _esbuild_bundle("enforce-stop")
        env = _stop_env(base)
        state_file = base / "state" / state_name
        env["GLUDD_STOP_STATE_FILE"] = str(state_file)
        result = _run_driver(
            _text_complete_driver(
                bundle,
                "CI failed but here's a summary",
                pre_js=_ci_cache_pre_js(ts_expression, env["GLUDD_WATCHDOG_CI_FILE"]),
            ),
            env,
            cwd=str(base / "project"),
        )
        assert state_file.exists(), (
            "hasRealPendingWork() must persist its WorkState diagnosis to "
            "GLUDD_STOP_STATE_FILE on every text.complete invocation"
        )
        work_state = json.loads(state_file.read_text())
        return result, work_state

    def test_text_complete_blocks_when_ci_red(self):
        base = _mk_workdir(unchecked_tasks=False)
        try:
            sanity_result, sanity_state = self._run_ci_leg(
                "Date.now()",
                base,
                "stop-state-ms.json",
            )
            assert sanity_state.get("ciVerdictPendingOrRed") is True, (
                "harness sanity: a millisecond-stamped FAILURE cache must "
                f"register as CI pending/red. Got workState: {sanity_state}"
            )
            _assert_blanked(sanity_result, "CI FAILURE (ms-format cache)")

            result, work_state = self._run_ci_leg(
                "Date.now() / 1000",
                base,
                "stop-state-seconds.json",
            )
            assert work_state.get("ciVerdictPendingOrRed") is True, (
                "CI RED IS INVISIBLE TO ENFORCEMENT: the watchdog writes "
                "last_ci_check via time.time() (epoch SECONDS, see "
                "scripts/agent_watchdog.py _update_ci_cache), but "
                "enforce-stop.ts compares it against Date.now() (ms) with a "
                "600_000ms window, so the real watchdog cache is always "
                "treated as stale. The plugin must normalize seconds/ms. "
                f"Got workState: {work_state}"
            )
            _assert_blanked(result, "CI FAILURE (watchdog seconds-format cache)")
        finally:
            shutil.rmtree(base, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# 4. enforce-multitask: the configured dispatch minimum is OPERATOR OPT-IN.
#    REQUIRED_DISPATCHES is active only when GLUDD_MIN_DISPATCHES /
#    GLUDD_MULTITASK_MIN_DISPATCHES is explicitly set (multitask_config.ts);
#    absent env vars mean no mandatory minimum (10 is the recommended default
#    and hard ceiling). When the minimum IS configured, an edit with zero
#    prior dispatches and pending TASKS.md work must be denied. Setting
#    GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables minimum enforcement — the
#    documented escape hatch.
# ═══════════════════════════════════════════════════════════════════════════


class TestMultitaskEnvVarEscapeHatch:
    """enforce-multitask.ts resolves REQUIRED_DISPATCHES only when the
    operator opts in via GLUDD_MIN_DISPATCHES / GLUDD_MULTITASK_MIN_DISPATCHES
    (multitask_config.ts:17). With no such env var, there is no mandatory
    minimum and an edit with zero prior dispatches passes through.

    With GLUDD_MULTITASK_MIN_DISPATCHES set AND
    GLUDD_MULTITASK_FLOOR_ENFORCE=1 (or unset), an edit with zero prior
    dispatches and unchecked TASKS.md items MUST be denied. With
    GLUDD_MULTITASK_FLOOR_ENFORCE=0, the block MUST be bypassed — the env var
    is the sanctioned escape hatch."""

    @staticmethod
    def _run_edit_leg(base: Path, extra_env: dict[str, str], state_name: str) -> dict:
        bundle = _esbuild_bundle("enforce-multitask")
        state = base / "state"
        env = {
            "GLUDD_PROJECT_ROOT": str(base / "project"),
            "GLUDD_MULTITASK_STATE_FILE": str(state / state_name),
            "GLUDD_DISENGAGE_PATH": str(state / "disengage-absent.json"),
            "GLUDD_ALIVE_PATH": str(state / "alive.json"),
            **extra_env,
        }
        return _run_driver(
            _multitask_edit_driver(bundle),
            env,
            cwd=str(base / "project"),
        )

    def test_edit_denied_with_zero_dispatches_env_enabled(self):
        base = _mk_workdir(unchecked_tasks=True)
        try:
            # Operator opts into a mandatory minimum via the min-dispatch env
            # var; with FLOOR_ENFORCE default (env unset → truthy) and
            # explicit env=1, an edit with 0 dispatches and unchecked
            # TASKS.md items MUST be denied (UNDER-FLOOR HARD BLOCK).
            for env in (
                {"GLUDD_MULTITASK_MIN_DISPATCHES": "2"},
                {
                    "GLUDD_MULTITASK_MIN_DISPATCHES": "2",
                    "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                },
            ):
                result_dict = self._run_edit_leg(base, env, "mt-state-on.json")
                result = result_dict.get("result")
                assert isinstance(result, dict) and result.get("permissionDecision") == "deny", (
                    f"env={env}: edit with 0 dispatches and pending work under "
                    f"an operator-configured minimum must be DENIED "
                    f"(UNDER-FLOOR HARD BLOCK). Got: {result_dict}"
                )
                assert result.get("message"), f"env={env}: deny must carry an actionable message. Got: {result_dict}"
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_edit_allowed_without_configured_minimum(self):
        base = _mk_workdir(unchecked_tasks=True)
        try:
            # No GLUDD_MIN_DISPATCHES / GLUDD_MULTITASK_MIN_DISPATCHES: the
            # minimum is opt-in, so an edit with zero prior dispatches and
            # pending TASKS.md work is ALLOWED.
            unconfigured = self._run_edit_leg(base, {}, "mt-state-no-min.json")
            result = unconfigured.get("result")
            if isinstance(result, dict):
                assert result.get("permissionDecision") != "deny", (
                    "without a configured minimum the under-floor block must "
                    f"not fire: edit should be ALLOWED. Got: {unconfigured}"
                )
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_edit_allowed_when_env_disabled(self):
        base = _mk_workdir(unchecked_tasks=True)
        try:
            # GLUDD_MULTITASK_FLOOR_ENFORCE=0 is the documented escape hatch:
            # the under-floor block MUST be bypassed even with a configured
            # minimum — the edit is ALLOWED despite zero dispatches + pending
            # TASKS.md work.
            bypassed = self._run_edit_leg(
                base,
                {
                    "GLUDD_MULTITASK_MIN_DISPATCHES": "2",
                    "GLUDD_MULTITASK_FLOOR_ENFORCE": "0",
                },
                "mt-state-off.json",
            )
            result = bypassed.get("result")
            # Allowed = no deny decision. Result may be None (passthrough) or a
            # non-deny dict; neither may carry permissionDecision == "deny".
            if isinstance(result, dict):
                assert result.get("permissionDecision") != "deny", (
                    "GLUDD_MULTITASK_FLOOR_ENFORCE=0 must disable the "
                    "under-floor block: edit should be ALLOWED, got "
                    f"permissionDecision='deny'. Got: {bypassed}"
                )
        finally:
            shutil.rmtree(base, ignore_errors=True)
