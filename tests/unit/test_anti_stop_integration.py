"""Integration tests for enforce-stop.ts text-only blocking with pending work.

Verifies the real plugin blanks text-only responses when TASKS.md has
unchecked items or config/ratchet.yml has entries, and allows them when
no pending work exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl

ROOT = Path(__file__).parent.parent.parent

pytestmark = pytest.mark.xdist_group("enforcement-shared-state")


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def test_hook_plugin_env_uses_per_test_hot_module_prefix(
    hook_plugin_env: HookEnv,
) -> None:
    """Hook tests must never load a live session's global hot module."""
    prefix = Path(hook_plugin_env.env["GLUDD_HOT_MODULE_PREFIX"])

    assert prefix.parent == hook_plugin_env.cwd
    assert prefix.name == "gludd-hot-"


def _invoke_text_complete(
    env: HookEnv,
    text: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict | None, str, str, int]:
    overrides = (env_overrides or {}).copy()
    result = env.invoke(
        "enforce-stop.ts",
        "experimental.text.complete",
        input={},
        output={"text": text, "toolCallMade": False, "dispatchCount": 0},
        env_overrides=overrides,
        timeout=15,
    )
    stdout_raw = result.stdout.strip()
    parsed = None
    if stdout_raw:
        with __import__("contextlib").suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _read_persist_block(env: HookEnv) -> dict | None:
    pb_path = env.state_path("GLUDD_PERSIST_STOP_BLOCK_FILE")
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


class TestTextOnlyBlockedWhenTASKSUnchecked:
    def test_blocked(self, hook_plugin_env: HookEnv):
        cwd = hook_plugin_env.cwd
        (cwd / "TASKS.md").write_text("- [ ] Fix critical bug\n")

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Looking at the remaining items we should prioritize the database migration first.",
        )
        assert rc == 0, stderr

        if parsed and parsed.get("text"):
            assert "BLOCKED" in parsed["text"] and "PENDING" in parsed["text"], (
                f"Expected blocking for text-only with pending TASKS.md. Got: {parsed}"
            )
        else:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"No parsed output and no persist block. raw={raw!r} stderr={stderr!r}"
            assert pb.get("blocked") is True, f"Persist block must be True. Got: {pb}"


class TestTextOnlyBlockedWhenRatchetEntries:
    def test_blocked(self, hook_plugin_env: HookEnv):
        cwd = hook_plugin_env.cwd
        (cwd / "config").mkdir(exist_ok=True)
        (cwd / "config" / "ratchet.yml").write_text("fixes:: 1\nknown_gap: yes\n")

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Reviewing the current status of the project across all active branches.",
        )
        assert rc == 0, stderr

        if parsed and parsed.get("text"):
            assert "BLOCKED" in parsed["text"] and "PENDING" in parsed["text"], (
                f"Expected blocking for text-only with ratchet entries. Got: {parsed}"
            )
        else:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"No parsed output and no persist block. raw={raw!r} stderr={stderr!r}"
            assert pb.get("blocked") is True, f"Persist block must be True. Got: {pb}"


class TestTextOnlyAllowedWhenNoPendingWork:
    def test_allowed(self, hook_plugin_env: HookEnv):
        cwd = hook_plugin_env.cwd
        (cwd / "TASKS.md").write_text("- [x] Everything done — 1 passed\n")
        (cwd / ".gate-status").write_text("=== GATE: PASSED ===\nlint PASS\ntypecheck PASS\ncollect PASS\ntest PASS\n")

        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Looking at the data pipeline architecture and how the components interact.",
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        if pb and pb.get("blocked"):
            pytest.fail(f"Should NOT block when no pending work exists. pb={pb} stdout={raw} stderr={stderr}")
