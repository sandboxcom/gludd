#!/usr/bin/env python3
"""Comprehensive subagent detection tests using the real shared.ts isSubagent().

Verifies the actual production ``isSubagent()`` from ``.opencode/lib/shared.ts``:
  1. OPENCODE_SUBAGENT=1 → true
  2. File marker (/tmp/gludd-subagent-<pid>.json) → true
  3. Neither env nor file → false
  4. Marker contents are opaque; existence alone → true
  5. Missing env + missing file → false (main thread)
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"

_tmp_counter = 0


def _run_ts(
    ts_body: str,
    env_override: dict[str, str | None] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Write a TS snippet that imports the real isSubagent, run via node, return parsed JSON."""
    global _tmp_counter
    _tmp_counter += 1
    full_code = f"""\
const {{ isSubagent }} = await import({json.dumps(str(SHARED_TS))})
;(async () => {{
{ts_body}
}})().catch(e => console.log(JSON.stringify({{__error__: String(e)}})))
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp",
        prefix=f"comprehensive_subagent_{_tmp_counter}_", delete=False,
    ) as f:
        f.write(full_code)
        tmp = f.name

    try:
        env = os.environ.copy()
        env.pop("OPENCODE_SUBAGENT", None)
        if env_override:
            for k, v in list(env_override.items()):
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        assert stdout, "Node subprocess returned no JSON result"
        parsed: object = json.loads(stdout)
        assert isinstance(parsed, dict), f"Expected JSON object, got {parsed!r}"
        return parsed
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ── Helper for file-marker tests ────────────────────────────────────────────

# ── Test 1: OPENCODE_SUBAGENT=1 → isSubagent returns true ───────────────────

def test_env_var_true() -> None:
    result = _run_ts(
        "console.log(JSON.stringify({isSub: isSubagent()}))",
        env_override={"OPENCODE_SUBAGENT": "1"},
    )
    assert result["isSub"] is True, f"Expected true for OPENCODE_SUBAGENT=1, got {result}"


# ── Test 2: File marker exists → isSubagent returns true ────────────────────

def test_file_marker_true() -> None:
    result = _run_ts(
        """\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${process.pid}.json`
fs.writeFileSync(marker, '{}')
const val = isSubagent()
try { fs.unlinkSync(marker) } catch {}
console.log(JSON.stringify({isSub: val}))
"""
    )
    assert result["isSub"] is True, f"Expected true when marker file exists, got {result}"


# ── Test 3: Neither env nor file → isSubagent returns false ─────────────────

def test_neither_false() -> None:
    result = _run_ts(
        """\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${process.pid}.json`
try { fs.unlinkSync(marker) } catch {}
console.log(JSON.stringify({isSub: isSubagent()}))
"""
    )
    assert result["isSub"] is False, f"Expected false with no signals, got {result}"


# ── Test 4: Marker contents are opaque; existence remains authoritative ──────

def test_marker_content_is_opaque() -> None:
    invalid_json = 'NOT VALID JSON ' + '{' * 13
    result = _run_ts(
        f"""\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${{process.pid}}.json`
fs.writeFileSync(marker, {json.dumps(invalid_json)})
const val = isSubagent()
try {{ fs.unlinkSync(marker) }} catch {{}}
console.log(JSON.stringify({{isSub: val}}))
"""
    )
    assert result["isSub"] is True, (
        f"Marker existence must signal subagent context regardless of contents, got {result}"
    )


# ── Test 5: Missing env + missing file → false (main thread) ────────────────

def test_main_thread_false() -> None:
    result = _run_ts(
        """\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${process.pid}.json`
try { fs.unlinkSync(marker) } catch {}
console.log(JSON.stringify({isSub: isSubagent()}))
""",
        env_override={"OPENCODE_SUBAGENT": None},
    )
    assert result["isSub"] is False, f"Expected false on main thread, got {result}"


# ── Edge case: OPENCODE_SUBAGENT=0 is NOT a subagent ────────────────────────

def test_env_var_zero_is_not_subagent() -> None:
    result = _run_ts(
        "console.log(JSON.stringify({isSub: isSubagent()}))",
        env_override={"OPENCODE_SUBAGENT": "0"},
    )
    assert result["isSub"] is False, f"OPENCODE_SUBAGENT=0 must be false, got {result}"


# ── Edge case: explicit env=0 disables marker fallback ─────────────────────

def test_env_zero_disables_file_marker_fallback() -> None:
    result = _run_ts(
        """\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${process.pid}.json`
fs.writeFileSync(marker, '{}')
const val = isSubagent()
try { fs.unlinkSync(marker) } catch {}
console.log(JSON.stringify({isSub: val}))
""",
        env_override={"OPENCODE_SUBAGENT": "0"},
    )
    assert result["isSub"] is False, (
        "The marker is a fallback only when OPENCODE_SUBAGENT is unset; "
        f"an explicit zero must win, got {result}"
    )


# ── Edge case: namespaced marker lookup isolates concurrent sessions ─────────

def test_namespaced_marker_ignores_stale_default_pid_marker(
    tmp_path: Path,
) -> None:
    marker_prefix = f"{tmp_path}/gludd-subagent-"
    result = _run_ts(
        """\
const fs = await import('node:fs')
const staleDefaultMarker = `/tmp/gludd-subagent-${process.pid}.json`
fs.writeFileSync(staleDefaultMarker, '{}')
const val = isSubagent()
try { fs.unlinkSync(staleDefaultMarker) } catch {}
console.log(JSON.stringify({isSub: val}))
""",
        env_override={
            "OPENCODE_SUBAGENT": "0",
            "GLUDD_SUBAGENT_MARKER_PREFIX": marker_prefix,
        },
    )
    assert result["isSub"] is False, (
        "A namespaced main-thread session must not inherit a stale default PID marker"
    )


# ── Edge case: env=1 overrides missing file ─────────────────────────────────

def test_env_overrides_missing_file() -> None:
    result = _run_ts(
        """\
const fs = await import('node:fs')
const marker = `/tmp/gludd-subagent-${process.pid}.json`
try { fs.unlinkSync(marker) } catch {}
console.log(JSON.stringify({isSub: isSubagent()}))
""",
        env_override={"OPENCODE_SUBAGENT": "1"},
    )
    assert result["isSub"] is True, f"Env=1 must yield true even with no file, got {result}"
