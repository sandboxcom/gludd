"""Behavior pin for the commit-serialization lock plugin.

Per AGENTS.md commit-serialization guardrail (Wave 12-14 incidents): parallel
subagents running `make ship-commit` race on the git index, causing staging
sweeps and misattributed commits. This plugin (LAYER 2) wraps commit-shaped
bash tool calls in an O_EXCL lock so only one commit runs at a time.

This test extracts COMMIT_TARGETS + STALE_THRESHOLD_MS from the TypeScript
source and asserts the deny/allow/stale-break contract. It also verifies the
Makefile `_commit-lock-acquire` target exists and is wired as a prereq to
every commit-shaped target (LAYER 1).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-commit-lock.ts"
MAKEFILE_PATH = ROOT / "Makefile"

EXPECTED_COMMIT_TARGETS = [
    "git-commit",
    "commit-no-verify",
    "git-commit-no-verify",
    "ship-commit",
    "repo-commit",
    "git-commit-file",
    "test-and-commit",
    "commit-bootstrap",
    "git-amend-msg",
]


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


def _extract_commit_targets(src: str) -> list[str]:
    block = re.search(
        r"COMMIT_TARGETS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)", src, re.DOTALL
    )
    assert block, "COMMIT_TARGETS export not found in plugin source"
    return re.findall(r'"([^"]+)"', block.group(1))


def _extract_stale_threshold_ms(src: str) -> int:
    expr = re.search(r"STALE_THRESHOLD_MS\s*=\s*([\d\s\*]+);", src)
    assert expr, "STALE_THRESHOLD_MS expression not found"
    return int(eval(expr.group(1).strip()))


def _matches_commit_command(cmd: str, targets: list[str]) -> bool:
    for target in targets:
        escaped = target.replace("-", r"\-")
        if re.search(rf"\bmake\s+{escaped}(?:\s|$)", cmd):
            return True
    return False


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-commit-lock.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_commit_targets(self):
        src = _plugin_source()
        assert "COMMIT_TARGETS" in src, "COMMIT_TARGETS export missing"

    def test_exports_stale_threshold(self):
        src = _plugin_source()
        assert "STALE_THRESHOLD_MS" in src, "STALE_THRESHOLD_MS export missing"

    def test_tool_execute_before_hook(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_tool_execute_after_hook(self):
        src = _plugin_source()
        assert "tool.execute.after" in src, "tool.execute.after hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable(self):
        src = _plugin_source()
        assert "GLUDD_COMMIT_LOCK_ENFORCE" in src, "Env-var disable switch missing"

    def test_env_var_path_override(self):
        src = _plugin_source()
        assert "GLUDD_COMMIT_LOCK_PATH" in src, "Env-var path override missing"

    def test_o_excl_create(self):
        src = _plugin_source()
        assert '"wx"' in src or "O_EXCL" in src, (
            "Must use O_EXCL exclusive create (fs.openSync 'wx')"
        )


class TestCommitTargetsList:
    def test_all_expected_targets_present(self):
        targets = _extract_commit_targets(_plugin_source())
        for expected in EXPECTED_COMMIT_TARGETS:
            assert expected in targets, f"Missing commit target: {expected}"

    def test_target_count_matches(self):
        targets = _extract_commit_targets(_plugin_source())
        assert len(targets) == len(EXPECTED_COMMIT_TARGETS), (
            f"Expected {len(EXPECTED_COMMIT_TARGETS)} targets, got {len(targets)}: {targets}"
        )


class TestDenyOnHeld:
    """Simulate the deny path: lock file exists, not stale → DENY."""

    @pytest.fixture(scope="class")
    def targets(self) -> list[str]:
        return _extract_commit_targets(_plugin_source())

    @pytest.mark.parametrize(
        "cmd",
        [
            "make ship-commit",
            "make ship-commit MSG='fix: update guardrail'",
            "make git-commit MSG='msg'",
            "make commit-no-verify MSG='msg'",
            "make git-commit-no-verify MSG='msg'",
            "make repo-commit MSG='msg'",
            "make git-commit-file FILE=/tmp/msg.txt",
            "make test-and-commit",
            "make commit-bootstrap MSG='msg'",
            "make git-amend-msg MSG='msg'",
        ],
    )
    def test_commit_command_detected(self, targets, cmd):
        assert _matches_commit_command(cmd, targets), f"Should detect: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "make lint",
            "make test-unit",
            "make gate",
            "make git-status",
            "make git-add FILES='src/foo.py'",
            "make git-log",
            "make ship-async REF=abc123",
        ],
    )
    def test_non_commit_command_not_detected(self, targets, cmd):
        assert not _matches_commit_command(cmd, targets), f"Should NOT detect: {cmd!r}"

    def test_ship_commit_files_not_matched(self, targets):
        """ship-commit-files is the atomic wrapper — it should NOT be in the
        lock list (it acquires the lock itself via _commit-lock-acquire prereq)."""
        assert "ship-commit-files" not in targets
        assert not _matches_commit_command(
            "make ship-commit-files FILES='src/foo.py'", targets
        )


class TestAllowOnFree:
    """When no lock file exists, the commit should be allowed (lock acquired)."""

    def test_try_acquire_logic(self, tmp_path):
        """Simulate the O_EXCL create + release cycle with a temp lock file."""
        lock = tmp_path / "test-commit.lock"

        # First create succeeds (lock acquired).
        fd = lock.open("x")
        fd.close()
        assert lock.exists()

        # Second O_EXCL create fails (lock held).
        with pytest.raises(FileExistsError):
            lock.open("x")

        # Release.
        lock.unlink()
        assert not lock.exists()

        # After release, create succeeds again.
        fd = lock.open("x")
        fd.close()
        assert lock.exists()


class TestStaleBreak:
    def test_stale_threshold_is_5_minutes(self):
        threshold = _extract_stale_threshold_ms(_plugin_source())
        assert threshold == 300000, f"Expected 300000ms (5 min), got {threshold}"

    def test_stale_break_logic_present(self):
        src = _plugin_source()
        assert "STALE_THRESHOLD_MS" in src
        assert "releaseLock" in src or "unlinkSync" in src, (
            "Must have stale-break logic (releaseLock/unlinkSync)"
        )

    def test_stale_break_flow(self, tmp_path):
        """Simulate the stale-break: old lock file → break → acquire."""
        import os
        import time

        lock = tmp_path / "stale.lock"
        lock.write_text("99999")
        # Set mtime to 10 minutes ago.
        old_time = time.time() - 600
        os.utime(lock, (old_time, old_time))

        stat = lock.stat()
        age_ms = (time.time() - stat.st_mtime) * 1000
        assert age_ms > 300000, "Lock should be stale (>5min)"

        # Break stale lock.
        lock.unlink()
        assert not lock.exists()

        # Acquire fresh.
        fd = lock.open("x")
        fd.close()
        assert lock.exists()


class TestMakefileLockTarget:
    def test_commit_lock_acquire_target_exists(self):
        assert MAKEFILE_PATH.exists()
        makefile = MAKEFILE_PATH.read_text()
        assert "_commit-lock-acquire" in makefile, (
            "_commit-lock-acquire target missing from Makefile"
        )

    @pytest.mark.parametrize("target", EXPECTED_COMMIT_TARGETS)
    def test_commit_target_has_lock_prereq(self, target):
        makefile = MAKEFILE_PATH.read_text()
        pattern = rf"^{target}:\s+.*_commit-lock-acquire"
        assert re.search(pattern, makefile, re.MULTILINE), (
            f"Target '{target}' missing _commit-lock-acquire prerequisite"
        )

    def test_ship_commit_files_target_exists(self):
        makefile = MAKEFILE_PATH.read_text()
        assert "ship-commit-files" in makefile, (
            "ship-commit-files target missing from Makefile"
        )

    def test_ship_commit_files_has_lock_prereq(self):
        makefile = MAKEFILE_PATH.read_text()
        pattern = r"^ship-commit-files:\s+_commit-lock-acquire"
        assert re.search(pattern, makefile, re.MULTILINE), (
            "ship-commit-files missing _commit-lock-acquire prereq"
        )

    def test_commit_lock_acquire_uses_flock_or_fcntl(self):
        makefile = MAKEFILE_PATH.read_text()
        target_block = re.search(
            r"_commit-lock-acquire:\n(.*?)(?=\n[a-zA-Z_-]+:|\Z)",
            makefile,
            re.DOTALL,
        )
        assert target_block, "_commit-lock-acquire recipe block not found"
        recipe = target_block.group(1)
        assert "flock" in recipe or "fcntl" in recipe, (
            "_commit-lock-acquire must use flock or fcntl fallback"
        )
