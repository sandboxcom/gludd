"""E2e test for enforce-no-suppressions.ts — lint-suppression comment blocking.

Invokes the actual TypeScript plugin via node --experimental-strip-types,
verifying the full deny/allow/subagent/allowlist cycle.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-suppressions.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"no_supp_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_HOT_MODULE_PREFIX"] = (
            f"/tmp/gludd-test-no-supp-e2e-{os.getpid()}-{_ts_counter}-"
        )
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or str(ROOT),
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        for line in reversed(stdout.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


_WRITE_TPL = """\
const mod = await import('{plugin_path}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'write'}},
  {{args: {{filePath: '{file_path}', content: '{content}'}}}}
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""

_EDIT_TPL = """\
const mod = await import('{plugin_path}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit'}},
  {{args: {{filePath: '{file_path}', newString: '{new_string}'}}}}
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""


def _write_code(file_path: str, content: str) -> str:
    return _WRITE_TPL.format(
        plugin_path=str(PLUGIN_PATH),
        file_path=file_path,
        content=content,
    )


def _edit_code(file_path: str, new_string: str) -> str:
    return _EDIT_TPL.format(
        plugin_path=str(PLUGIN_PATH),
        file_path=file_path,
        new_string=new_string,
    )


# ───


def test_noqa_blocked_on_write():
    """Hook denies write containing # noqa in src/ file."""
    code = _write_code("src/fake.py", "# noqa: E501  # silence lint")
    result = _run_plugin(code)
    assert result is not None, "Expected deny result for # noqa"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "suppression" in result.get("message", "").lower(), (
        f"Deny message must mention suppression: {result.get('message')}"
    )


def test_noqa_blocked_on_edit():
    """Hook denies edit containing # noqa in tests/ file."""
    code = _edit_code("tests/fake.py", "# noqa  # suppress all")
    result = _run_plugin(code)
    assert result is not None, "Expected deny result for # noqa on edit"
    assert result.get("permissionDecision") == "deny"


# ─── # type: ignore blocked ──────────────────────────────────────────────────


def test_type_ignore_blocked_on_write():
    """Hook denies write containing # type: ignore."""
    code = _write_code("src/models.py", "# type: ignore[assignment]")
    result = _run_plugin(code)
    assert result is not None, "Expected deny result for # type: ignore"
    assert result.get("permissionDecision") == "deny"


def test_type_ignore_blocked_on_edit():
    """Hook denies edit containing # type: ignore."""
    code = _edit_code("tests/test_foo.py", "# type: ignore")
    result = _run_plugin(code)
    assert result is not None, "Expected deny result for # type: ignore on edit"
    assert result.get("permissionDecision") == "deny"


# ─── Plain comment allowed ───────────────────────────────────────────────────


def test_plain_comment_allowed_on_write():
    """Hook allows write containing plain comment (non-suppression)."""
    code = _write_code("src/utils.py", "# regular comment explaining logic")
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Plain comment should be allowed, got: {result}"
    )


def test_plain_comment_allowed_on_edit():
    """Hook allows edit containing plain comment."""
    code = _edit_code("src/helpers.py", "# TODO: refactor this")
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Plain comment should be allowed on edit, got: {result}"
    )


def test_no_comment_text_allowed():
    """Hook allows write with no comment at all."""
    code = _write_code("src/app.py", "x = 1")
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Non-comment text should be allowed, got: {result}"
    )


def test_suppression_text_inside_string_literal_allowed():
    """Suppression-shaped data inside a quoted string is not a comment."""
    code = _write_code("src/app.py", 'HASH_PREFIX = "#noqa"')
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Quoted suppression data should be allowed, got: {result}"
    )


def test_suppression_text_inside_docstring_allowed():
    """Suppression-shaped prose inside a docstring is not a comment."""
    code = _write_code("src/app.py", '"""Documentation mentions # noqa safely."""')
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Docstring suppression prose should be allowed, got: {result}"
    )


# ─── Allowlisted file bypasses check ─────────────────────────────────────────


def test_allowlisted_file_bypasses_noqa():
    """Allowlisted path (fix_not_disable.py) allows # noqa."""
    code = _write_code(
        "src/general_ludd/security/fix_not_disable.py",
        "# noqa  # this file stores patterns as data",
    )
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Allowlisted file should allow # noqa, got: {result}"
    )


def test_allowlisted_file_bypasses_type_ignore():
    """Allowlisted path (test_type_safety_guardrails.py) allows # type: ignore."""
    code = _write_code(
        "tests/unit/test_type_safety_guardrails.py",
        "# type: ignore  # pattern fixture",
    )
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Allowlisted file should allow # type: ignore, got: {result}"
    )


def test_non_allowlisted_file_still_blocked():
    """Similar path not in allowlist is still blocked."""
    code = _write_code(
        "tests/unit/test_other_guardrails.py",
        "# noqa  # not in allowlist",
    )
    result = _run_plugin(code)
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Non-allowlisted similar path should be denied, got: {result}"
    )


# ─── Subagent guard ──────────────────────────────────────────────────────────


def test_subagent_skips_check():
    """OPENCODE_SUBAGENT=1 bypasses suppression check entirely."""
    code = _write_code("src/fake.py", "# noqa: E501  # should be blocked normally")
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip check, got: {result}"
    )


# ─── Environment cannot disable a hard guardrail ─────────────────────────────


def test_env_disable_cannot_bypass_check():
    """GLUDD_NO_SUPPRESSIONS_ENFORCE=0 cannot disable the hard guardrail."""
    code = _write_code("src/fake.py", "# noqa: E501  # must remain blocked")
    result = _run_plugin(code, env_override={"GLUDD_NO_SUPPRESSIONS_ENFORCE": "0"})
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Environment override must not bypass suppression blocking: {result}"
    )


# ─── Fail-open: empty content ────────────────────────────────────────────────


def test_empty_content_fails_open():
    """Empty content returns void (fail-open, allows)."""
    code = _write_code("src/fake.py", "")
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Empty content should be allowed (fail-open), got: {result}"
    )


# ─── Non-edit/write tools not blocked ────────────────────────────────────────


def test_read_tool_not_blocked():
    """Hook only fires on edit/write; read tool always passes through."""
    read_code = f"""\
const mod = await import('{PLUGIN_PATH!s}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: 'src/fake.py'}}}}
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(read_code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Read tool should not be blocked, got: {result}"
    )


# ─── Other suppression patterns ──────────────────────────────────────────────


def test_pylint_disable_blocked():
    """Hook blocks # pylint: disable=..."""
    code = _write_code("src/foo.py", "# pylint: disable=missing-docstring")
    result = _run_plugin(code)
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"# pylint: should be blocked, got: {result}"
    )


def test_fmt_off_blocked():
    """Hook blocks # fmt: off."""
    code = _write_code("src/bar.py", "# fmt: off")
    result = _run_plugin(code)
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"# fmt: off should be blocked, got: {result}"
    )


def test_isort_skip_blocked():
    """Hook blocks # isort:skip."""
    code = _write_code("src/baz.py", "# isort:skip")
    result = _run_plugin(code)
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"# isort:skip should be blocked, got: {result}"
    )
