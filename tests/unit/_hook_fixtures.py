"""Shared fixtures for the CI-runnable hook-liveness harness (Wave E).

Provides:
  * ``NODE_OK`` / ``HOOK_LIVE_SKIP_REASON`` — probed ONCE per test session
    (module import time) so every test that needs a live Node process to
    exercise a plugin skips cleanly (not fails) on a machine without a
    sufficiently new Node (`node --experimental-strip-types` requires >=
    22.6) rather than erroring out.
  * ``hook_plugin_env`` — a pytest fixture yielding a ``HookEnv``: an isolated
    tmp cwd plus an env dict that redirects every plugin state-file env var
    (verified against the actual `.opencode/plugin/*.ts` / `.opencode/plugins/*.ts`
    source — see ``STATE_ENV_VARS`` below) into that tmp dir, so hook-liveness
    tests never read or clobber a live opencode session's real
    ``/tmp/gludd-*.json`` state.
  * ``HookEnv.invoke(plugin, hook, input=..., output=...)`` — runs
    ``scripts/hook_plugin_harness.mjs`` as a subprocess against the given
    plugin file and hook name, returning the completed subprocess. Several
    plugins implement "deny" by *throwing* (non-zero exit) rather than
    returning a value — callers assert on ``.returncode``/``.stderr`` for
    those, and on the JSON-decoded ``.stdout`` for the return-value ones.
  * ``teardown_watchdog_session(env)`` — fires watchdog.ts's
    ``session.deleted`` event so a per-test-spawned watchdog child (if any)
    is reaped. MANDATORY after any test that fires watchdog.ts's
    ``session.created`` event.

SAFETY NOTE (hardcoded /tmp paths): a handful of plugin state files are
hardcoded absolute ``/tmp/gludd-*.json`` paths with NO env-var override (e.g.
enforce-stop.ts's BLOCK_COUNTER_FILE, STOP_TOOL_COUNTS file; watchdog.ts's PID
file). These are SHARED with any live opencode session running on the same
machine. ``hook_plugin_env`` snapshots every path in ``HARDCODED_TMP_PATHS``
at setup and restores the exact original bytes (or absence) at teardown, so a
test run can never leave net-visible contamination in a live session's state,
even though the plugin code itself briefly reads/writes the real path during
the test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS = ROOT / "scripts" / "hook_plugin_harness.mjs"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

_MIN_NODE = (22, 6)


def _parse_node_version(raw: str) -> tuple[int, int, int] | None:
    raw = raw.strip().lstrip("v")
    parts = raw.split(".")
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _probe_node() -> tuple[int, int, int] | None:
    """Probe the installed node's version ONCE (module import time). Returns
    None if node is missing, unparsable, or errors — every caller treats that
    as "skip", never "fail"."""
    node_bin = shutil.which("node")
    if not node_bin:
        return None
    try:
        proc = subprocess.run(
            [node_bin, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_node_version(proc.stdout)


_NODE_VERSION = _probe_node()
NODE_OK = _NODE_VERSION is not None and _NODE_VERSION >= _MIN_NODE
HOOK_LIVE_SKIP_REASON = (
    f"node --experimental-strip-types requires node >= "
    f"{_MIN_NODE[0]}.{_MIN_NODE[1]}; found "
    f"{'.'.join(str(p) for p in _NODE_VERSION) if _NODE_VERSION else 'no node on PATH'}"
)

# A node binary reporting a version >= _MIN_NODE is NECESSARY but not
# SUFFICIENT: `--experimental-strip-types` only STRIPS type annotations, it
# does not transpile — so a Node runtime that easily clears the version probe
# above can still throw on real TS constructs (enums, namespaces, parameter
# properties, `import =`) that the .opencode/plugin/*.ts sources may use. This
# bit CI on a fresh 2026-07 run: setup-node gave CI node 22, which passed the
# version probe above but choked on the actual plugin sources, turning every
# harness-driven test into a hard failure instead of a clean skip. The probe
# below actually EXERCISES the harness once against a known-good plugin/hook
# so that failure mode is caught at the same "environment precondition"
# layer, rather than propagating into ~33 individual test failures.
_HARNESS_ERROR_SIGNATURES = (
    "SyntaxError",
    "Unsupported",
    "ERR_UNSUPPORTED",
    "experimental-strip-types",
    "Cannot find",
    "MODULE_NOT_FOUND",
    "ERR_MODULE_NOT_FOUND",
    "is not defined",
)

# Plugin state-file env vars, VERIFIED against the actual source of every
# plugin this harness exercises (name -> defining plugin):
#   GLUDD_STOP_STATE_FILE          enforce-stop.ts      (STATE_FILE, session.idle)
#   GLUDD_STREAK_FILE              enforce-stop.ts / enforce-floor.ts (SHARED_STREAK_FILE)
#   GLUDD_TASK_DEADLINE_STATE      enforce-deadline.ts  (DEADLINE_STATE)
#   GLUDD_TASK_DEADLINE_WARNINGS   enforce-deadline.ts  (WARNINGS_LOG)
#   GLUDD_TASK_STALE_FILE          enforce-deadline.ts  (STALE_FILE)
#   GLUDD_SESSION_STATE            enforce-session-start.ts (STATE_FILE)
#   GLUDD_MAINTHREAD_STREAK_FILE   enforce-delegate.ts  (MAINTHREAD_STREAK_FILE)
#   GLUDD_FORCE_DELEGATE_STATE     enforce-delegate.ts  (FORCE_DELEGATE_STATE)
#   GLUDD_MODEL_UTIL_STATE         enforce-delegate.ts  (MODEL_UTIL_STATE)
#   GLUDD_READ_GRIND_FILE          enforce-delegate.ts  (READ_GRIND_FILE)
#   GLUDD_SONNET_TARGET_CONFIG     enforce-delegate.ts  (config file, shadowed by
#                                                        GLUDD_SONNET_TARGET_SHARE)
#   GLUDD_MAIN_MODEL_FILE          enforce-delegate.ts  (main-model marker file)
# GLUDD_DISK_FREE_OVERRIDE / GLUDD_VENV_COUNT_OVERRIDE are VALUE overrides (not
# file paths) read by enforce-delegate.ts's diskSnapshot() — setting them
# bypasses a real `shutil.disk_usage` subprocess exec unconditionally.
STATE_FILE_ENV_VARS = [
    "GLUDD_STOP_STATE_FILE",
    "GLUDD_STREAK_FILE",
    "GLUDD_TASK_DEADLINE_STATE",
    "GLUDD_TASK_DEADLINE_WARNINGS",
    "GLUDD_TASK_STALE_FILE",
    "GLUDD_SESSION_STATE",
    "GLUDD_MAINTHREAD_STREAK_FILE",
    "GLUDD_FORCE_DELEGATE_STATE",
    "GLUDD_MODEL_UTIL_STATE",
    "GLUDD_READ_GRIND_FILE",
    "GLUDD_SONNET_TARGET_CONFIG",
    "GLUDD_MAIN_MODEL_FILE",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT",
    "GLUDD_FLOOR_TEXT_COMPLETE_COUNT",
    "GLUDD_BLOCK_COUNTER_FILE",
    "GLUDD_BLOCK_REASON_FILE",
    "GLUDD_PERSIST_STOP_BLOCK_FILE",
    "GLUDD_FORCE_DISPATCH_PATH",
    "GLUDD_RELEASE_COMPLETENESS_FILE",
    "GLUDD_LAST_TEST_RESULT_FILE",
    "GLUDD_MULTITASK_STATE_FILE",
    "GLUDD_POST_RESULTS_STATE_FILE",
    "GLUDD_TEXT_ONLY_STATE_FILE",
    "GLUDD_WATCHDOG_CI_FILE",
    "GLUDD_STOP_TOOL_COUNTS_FILE",
    "GLUDD_WATCHDOG_PID_FILE",
]

# Absolute /tmp paths hardcoded in plugin source with NO env-var override.
# hook_plugin_env snapshots + restores every one of these around each test so
# a hook-liveness test can never leave net contamination in a live session's
# shared /tmp/gludd-*.json state (see module docstring SAFETY NOTE).
HARDCODED_TMP_PATHS = [
    # BLOCK_REASON_FILE / BLOCK_COUNTER_FILE / stop-tool-counts / watchdog.pid
    # moved to STATE_FILE_ENV_VARS (env-redirected per-test) — they were read
    # back by tests and raced under xdist when a sibling worker's _restore()
    # unlinked the shared /tmp file mid-test (CI run 29110591188 unit-2 and
    # 29123726253 unit-2 FileNotFoundError on /tmp/gludd-watchdog.pid).
    "/tmp/gludd-blanked-responses.json",     # enforce-stop.ts BLANKED_RESPONSE_FILE
    "/tmp/gludd-force-dispatch.json",        # enforce-stop.ts / enforce-delegate.ts
    "/tmp/gludd-false-done-blocks.json",     # enforce-stop.ts FALSE_DONE_BLOCKS_FILE
    "/tmp/gludd-plugin-alive.json",          # _reportAlive() shared across plugins
    "/tmp/gludd-plugin-heartbeat-enforce-stop.json",
    "/tmp/gludd-plugin-heartbeat-enforce-delegate.json",
    "/tmp/gludd-plugin-heartbeat-enforce-session-start.json",
    "/tmp/gludd-plugin-loaded.log",
]

# Env vars that must NEVER leak from the ambient dev/agent shell into an
# isolated hook invocation — this repo's own live session sets many of these
# (CLAUDE_AGENT_FLOOR, GLUDD_FORCE_DELEGATE, etc.) and a test must control its
# own knobs deterministically rather than inherit whatever the orchestrating
# session happens to have set.
_STRIP_ENV_PREFIXES = ("GLUDD_", "CLAUDE_", "OPENCODE_")
_STRIP_ENV_EXACT = {"DELETION_REASON"}


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k.startswith(p) for p in _STRIP_ENV_PREFIXES)
        and k not in _STRIP_ENV_EXACT
    }


def _resolve_plugin_path(plugin: str) -> str:
    """Resolve a plugin argument to an absolute path: absolute paths pass
    through; ROOT-relative paths (e.g. ".opencode/plugin/enforce-stop.ts")
    resolve directly; a bare filename (e.g. "enforce-stop.ts") is resolved by
    searching PLUGIN_DIR then PLUGINS_DIR."""
    if os.path.isabs(plugin):
        return plugin
    candidate = ROOT / plugin
    if candidate.exists():
        return str(candidate)
    for directory in (PLUGIN_DIR, PLUGINS_DIR):
        alt = directory / plugin
        if alt.exists():
            return str(alt)
    return str(candidate)


@dataclass
class HookEnv:
    cwd: Path
    env: dict[str, str]

    def invoke(
        self,
        plugin: str,
        hook: str,
        input: dict | None = None,
        output: dict | None = None,
        env_overrides: dict[str, str] | None = None,
        repeat: int = 1,
        timeout: int = 10,
    ) -> subprocess.CompletedProcess[str]:
        """Run scripts/hook_plugin_harness.mjs against `plugin` (an absolute
        path, a path relative to the repo ROOT such as
        ".opencode/plugin/enforce-stop.ts", OR a bare filename such as
        "enforce-stop.ts" — resolved by searching PLUGIN_DIR then
        PLUGINS_DIR), invoking `hook` with the given input/output dicts.
        ``repeat`` invokes the hook multiple times in one loaded Node process,
        so PID-scoped state behaves like one OpenCode session.
        Returns the completed subprocess — never raises on a non-zero exit,
        since several plugins implement "deny" by throwing; the caller
        asserts on `.returncode`/`.stderr` for those."""
        plugin_path = _resolve_plugin_path(plugin)
        args = [
            "node",
            "--experimental-strip-types",
            str(HARNESS),
            plugin_path,
            hook,
            json.dumps(input or {}),
            json.dumps(output or {}),
            str(repeat),
        ]
        run_env = dict(self.env)
        if env_overrides:
            run_env.update(env_overrides)
        return subprocess.run(
            args,
            cwd=str(self.cwd),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def state_path(self, env_var: str) -> Path:
        return Path(self.env[env_var])

    def read_json(self, env_var: str) -> dict | list | None:
        p = self.state_path(env_var)
        if not p.exists():
            return None
        return json.loads(p.read_text())


def read_optional_bytes(path: str | Path) -> bytes | None:
    """Read bytes or return None when a concurrently removed file is absent.

    Reading directly and handling ``FileNotFoundError`` avoids the
    ``exists()``/``read_bytes()`` TOCTOU window shared enforcement-state tests
    previously hit under xdist.
    """
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _snapshot(paths: list[str]) -> dict[str, bytes | None]:
    snap: dict[str, bytes | None] = {}
    for p in paths:
        snap[p] = read_optional_bytes(p)
    return snap


def _restore(snap: dict[str, bytes | None]) -> None:
    for p, original in snap.items():
        path_obj = Path(p)
        try:
            if original is None:
                if path_obj.exists():
                    path_obj.unlink()
            else:
                path_obj.write_bytes(original)
        except OSError:
            pass  # best-effort restore — never fail test teardown


def _probe_harness() -> str | None:
    """Actually EXERCISE scripts/hook_plugin_harness.mjs once, session-scoped
    (module import time), against a known-good plugin/hook
    (enforce-stop.ts's `event` handler on `session.idle`) rather than merely
    checking the node version. Returns None when the harness genuinely works
    end-to-end; otherwise a human-readable reason string suitable for
    `pytest.skip`.

    Snapshots + restores HARDCODED_TMP_PATHS around the probe invocation
    (same guard `hook_plugin_env_impl` uses per-test) since enforce-stop.ts's
    `session.idle` handler touches several of those hardcoded /tmp state
    files with no env-var override.
    """
    if not NODE_OK:
        return HOOK_LIVE_SKIP_REASON

    node_bin = shutil.which("node")
    plugin_path = _resolve_plugin_path("enforce-stop.ts")
    args = [
        node_bin,
        "--experimental-strip-types",
        str(HARNESS),
        plugin_path,
        "event",
        json.dumps({"event": {"type": "session.idle"}}),
        json.dumps({}),
    ]
    snap = _snapshot(HARDCODED_TMP_PATHS)
    try:
        try:
            proc = subprocess.run(
                args,
                cwd=str(ROOT),
                env=_clean_base_env(),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"harness probe invocation raised {exc!r}"
    finally:
        _restore(snap)

    stderr = proc.stderr or ""
    if any(sig in stderr for sig in _HARNESS_ERROR_SIGNATURES):
        return (
            "harness probe against enforce-stop.ts/session.idle failed with an "
            f"environment/parse error (exit {proc.returncode}): "
            f"{stderr.strip()[:500]!r}"
        )
    if proc.returncode != 0:
        return (
            "harness probe against enforce-stop.ts/session.idle exited "
            f"{proc.returncode} (not a recognized deny-by-throw signature): "
            f"{stderr.strip()[:500]!r}"
        )
    return None


# Session-scoped (module import time, like NODE_OK above): every
# harness-driven test skips together via HOOK_LIVE_SKIP_REASON when the
# harness can't actually run the real plugin sources in this environment —
# e.g. CI's Node 22 passes the version probe but its `--experimental-strip-
# types` is strip-only (no transpile) and throws on TS constructs the plugin
# sources use. On a working local environment this stays None so the tests
# keep running for real (not skipping) and the local coverage isn't lost.
_HARNESS_PROBE_FAILURE = _probe_harness()
HARNESS_OK = _HARNESS_PROBE_FAILURE is None
if _HARNESS_PROBE_FAILURE is not None:
    HOOK_LIVE_SKIP_REASON = _HARNESS_PROBE_FAILURE


def hook_plugin_env_impl(tmp_path: Path):
    """Generator body for the `hook_plugin_env` fixture.

    NOTE: this is intentionally NOT decorated with `@pytest.fixture` here.
    Importing an already-fixture-decorated function of the same name into
    multiple test modules trips ruff's F811 ("redefinition of unused name")
    on every test function parameter that shadows it. Instead, each
    consuming test module defines its OWN thin `@pytest.fixture def
    hook_plugin_env(tmp_path): yield from hook_plugin_env_impl(tmp_path)` —
    see test_hook_runtime_verification.py / test_hooks_actually_fire.py.

    Isolated tmp cwd + env redirecting every overridable plugin state file
    into it, PLUS a snapshot/restore guard around the small set of hardcoded
    /tmp paths plugins also touch unconditionally (see module docstring).

    Skips (does not fail) when node is missing/too old for
    `--experimental-strip-types`, OR when the session-scoped harness probe
    (`_probe_harness`, see `HARNESS_OK` / `HOOK_LIVE_SKIP_REASON` above) found
    that a real invocation against a known-good plugin/hook errors out (e.g.
    a Node whose `--experimental-strip-types` can't handle this repo's TS
    constructs) — so every harness-driven test skips together with a clear
    reason instead of erroring out individually.
    """
    if not HARNESS_OK:
        pytest.skip(f"hook harness unavailable in this environment: {HOOK_LIVE_SKIP_REASON}")

    snap = _snapshot(HARDCODED_TMP_PATHS)

    env = _clean_base_env()
    # Keep filesystem-backed enforcement checks inside the per-test project.
    # Without this override getProjectRoot() falls back to the live main
    # checkout when a clean fixture intentionally has no TASKS.md, making
    # otherwise isolated hook tests inherit real pending work.
    env["GLUDD_PROJECT_ROOT"] = str(tmp_path)
    # Never let a hook test import /tmp/gludd-hot-*.js from a live OpenCode
    # session.  The tmp_path fixture is function-scoped, so this prefix is
    # isolated per test while still exercising the normal fallback path.
    env["GLUDD_HOT_MODULE_PREFIX"] = str(tmp_path / "gludd-hot-")
    for var in STATE_FILE_ENV_VARS:
        env[var] = str(tmp_path / f"{var.lower()}.json")
    # Blanket disk-safety overrides — bypass the real disk_usage subprocess
    # exec unconditionally so no test result depends on this machine's
    # actual free disk / worktree venv count.
    env["GLUDD_DISK_FREE_OVERRIDE"] = "999"
    env["GLUDD_VENV_COUNT_OVERRIDE"] = "0"
    # Deterministic sonnet-ratio target unless a test overrides it — takes
    # precedence over GLUDD_SONNET_TARGET_CONFIG (see readTargetShare()).
    env.setdefault("GLUDD_SONNET_TARGET_SHARE", "0.5")

    try:
        yield HookEnv(cwd=tmp_path, env=env)
    finally:
        _restore(snap)


def invoke_text_complete_and_confirm_increment(
    hook_plugin_env: HookEnv,
    plugin: str,
    counter_path: str,
    attempts: int = 5,
) -> None:
    """Drive `plugin`'s experimental.text.complete hook twice and confirm the
    HARDCODED fire-counter file at `counter_path` strictly increases.

    That file has NO env-var override (see HARDCODED_TMP_PATHS) and is
    therefore shared with any live opencode session running on this same
    machine — a genuine concurrent writer (e.g. the very session driving
    this test suite) can occasionally race a single pair of calls to a tie.
    Retrying clears a transient race almost immediately; a hook that is
    actually dead fails every attempt, so this stays a hard, meaningful
    assertion rather than a weakened `>=`.
    """
    last: tuple[int | None, int | None] = (None, None)
    counter = Path(counter_path)
    for _ in range(attempts):
        r1 = hook_plugin_env.invoke(
            plugin, "experimental.text.complete",
            input={}, output={"text": "first in-progress response"},
        )
        assert r1.returncode == 0, r1.stderr
        count1 = json.loads(counter.read_text()).get("count", 0)

        r2 = hook_plugin_env.invoke(
            plugin, "experimental.text.complete",
            input={}, output={"text": "second in-progress response"},
        )
        assert r2.returncode == 0, r2.stderr
        count2 = json.loads(counter.read_text()).get("count", 0)

        last = (count1, count2)
        if count2 > count1:
            return
    count1, count2 = last
    raise AssertionError(
        f"{plugin} experimental.text.complete counter did not increase "
        f"across {attempts} retried attempts: {count1} -> {count2}"
    )


def teardown_watchdog_session(env: HookEnv) -> subprocess.CompletedProcess[str]:
    """Fire watchdog.ts's `session.deleted` event so a per-test-spawned
    watchdog child (if the test's `session.created` invocation actually
    spawned one) is reaped. MANDATORY for any test that invokes watchdog.ts's
    `session.created` event handler.
    """
    return env.invoke(
        ".opencode/plugins/watchdog.ts",
        "event",
        input={"event": {"type": "session.deleted"}},
    )
