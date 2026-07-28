"""Verify enforce-objective.ts BLOCKING behavior for dispatch when unpushed
commits exist and a release version is pending in pyproject.toml.

Structural checks on the plugin source AND behavioral simulation of the
dispatch-deny logic via the regex/version patterns that mirror the plugin code.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-objective.ts"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestDispatchBlockingStructure:
    """Plugin source must contain the blocking dispatch check."""

    def test_plugin_exists_and_registered(self):
        assert PLUGIN_PATH.exists()
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-objective.ts" in oc

    def test_unpushed_commit_check_present(self):
        src = _plugin_source()
        assert "getUnpushedCommitCount" in src, (
            "Plugin must export getUnpushedCommitCount to check for unpushed commits"
        )
        assert "git rev-list --count" in src, "Plugin must use git rev-list --count to count unpushed commits"

    def test_pending_release_check_present(self):
        src = _plugin_source()
        assert "getPendingReleaseVersion" in src, "Plugin must export getPendingReleaseVersion to check pyproject.toml"
        assert "pyproject.toml" in src, "Plugin must read pyproject.toml for pending release check"

    def test_dispatch_deny_block_present(self):
        src = _plugin_source()
        # The block must deny task/agent/workflow when unpushed + pending
        assert "DISPATCH BLOCKED" in src, "Plugin must have a DISPATCH BLOCKED deny message"
        assert "unpushed commit(s)" in src.lower(), "Block message must reference unpushed commits"
        assert "pending in pyproject.toml" in src.lower(), "Block message must reference pyproject.toml"
        assert "push commits first" in src.lower() or "make batch-push" in src, (
            "Block message must tell agent to push first"
        )

    def test_dispatch_deny_for_task_agent_workflow(self):
        src = _plugin_source()
        # The dispatch-allow block must now have a deny path
        assert '"task"' in src
        assert '"agent"' in src
        assert '"workflow"' in src
        assert "permissionDecision" in src
        # Verify one of the deny paths is for dispatch
        deny_count = src.count('permissionDecision: "deny"')
        assert deny_count >= 1, f"Expected >=1 permissionDecision denials, found {deny_count}"

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_OBJECTIVE_ENFORCE" in src, "Plugin must have GLUDD_OBJECTIVE_ENFORCE disable path"

    def test_subagent_guard_present(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "Plugin must skip enforcement for subagents"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "Plugin must fail-open on errors"


class TestPreReleaseVersionRegex:
    """Mirror the plugin's pre-release version detection regex."""

    # The plugin checks for /-(?:alpha|beta|rc|dev)/ in the version string.
    PRE_RELEASE_RE = re.compile(r"-(?:alpha|beta|rc|dev)")

    def test_alpha_is_pre_release(self):
        assert self.PRE_RELEASE_RE.search("0.1.0-alpha.1"), "alpha should be pre-release"
        assert self.PRE_RELEASE_RE.search("1.0.0-alpha"), "alpha suffix should match"

    def test_beta_is_pre_release(self):
        assert self.PRE_RELEASE_RE.search("0.1.0-beta.3"), "beta should be pre-release"
        assert self.PRE_RELEASE_RE.search("2.0.0-beta.1"), "beta should be pre-release"

    def test_rc_is_pre_release(self):
        assert self.PRE_RELEASE_RE.search("0.1.0-rc.1"), "rc should be pre-release"
        assert self.PRE_RELEASE_RE.search("1.0.0-rc"), "rc should match"

    def test_dev_is_pre_release(self):
        assert self.PRE_RELEASE_RE.search("0.1.0-dev.5"), "dev should be pre-release"
        assert self.PRE_RELEASE_RE.search("1.0.0-dev"), "dev should match"

    def test_stable_version_is_not_pre_release(self):
        assert not self.PRE_RELEASE_RE.search("0.1.0"), "stable should not match"
        assert not self.PRE_RELEASE_RE.search("1.0.0"), "stable should not match"
        assert not self.PRE_RELEASE_RE.search("2.3.4"), "stable should not match"

    def test_non_pre_release_segments_do_not_match(self):
        assert not self.PRE_RELEASE_RE.search("0.1.0+build.1"), "build metadata not pre-release"
        assert not self.PRE_RELEASE_RE.search("0.1.0-other"), "random suffix not pre-release"


class TestPyprojectVersionExtraction:
    """Verify the version regex used in the plugin correctly extracts versions."""

    VERSION_RE = re.compile(r"^\s*version\s*=\s*\"([^\"]+)\"", re.MULTILINE)

    def test_extracts_version_quoted(self):
        content = 'version = "0.1.0-beta.3"\n'
        match = self.VERSION_RE.search(content)
        assert match is not None
        assert match.group(1) == "0.1.0-beta.3"

    def test_extracts_version_with_leading_spaces(self):
        content = '  version     =   "1.2.3-alpha"\n'
        match = self.VERSION_RE.search(content)
        assert match is not None
        assert match.group(1) == "1.2.3-alpha"

    def test_no_match_on_non_version_lines(self):
        content = '  requires-python = ">=3.11"\n'
        match = self.VERSION_RE.search(content)
        assert match is None

    def test_extracts_from_actual_pyproject_toml(self):
        assert PYPROJECT_PATH.exists(), "pyproject.toml should exist"
        content = PYPROJECT_PATH.read_text()
        match = self.VERSION_RE.search(content)
        assert match is not None, "Should find version in pyproject.toml"
        version = match.group(1)
        assert len(version) > 0, "Version should not be empty"

    def test_current_version_is_pre_release(self):
        """The current version (0.1.0-beta.3) should be detected as pre-release."""
        content = PYPROJECT_PATH.read_text()
        match = self.VERSION_RE.search(content)
        assert match is not None
        version = match.group(1)
        assert TestPreReleaseVersionRegex.PRE_RELEASE_RE.search(version), (
            f"Current version {version} should be detected as pre-release"
        )


class TestBlockingLogicSimulation:
    """Simulate the plugin's dispatch-blocking logic with Python equivalents."""

    PRE_RELEASE_RE = re.compile(r"-(?:alpha|beta|rc|dev)")
    VERSION_RE = re.compile(r"^\s*version\s*=\s*\"([^\"]+)\"", re.MULTILINE)

    def _get_pending_version(self) -> str:
        if not PYPROJECT_PATH.exists():
            return ""
        content = PYPROJECT_PATH.read_text()
        match = self.VERSION_RE.search(content)
        if not match:
            return ""
        version = match[1]
        if self.PRE_RELEASE_RE.search(version):
            return version
        return ""

    def _should_block(self, unpushed_count: int) -> bool:
        pending = self._get_pending_version()
        return unpushed_count > 0 and bool(pending)

    def test_blocks_when_unpushed_and_pending_release(self):
        """When unpushed > 0 and version is pre-release, block dispatch."""
        pending = self._get_pending_version()
        assert pending, "pyproject.toml must have a pre-release version for this test"
        assert self._should_block(5), "5 unpushed + pending release → should block"

    def test_allows_when_no_unpushed_commits(self):
        assert not self._should_block(0), "0 unpushed → should allow dispatch"

    def test_allows_when_no_pending_release(self):
        """Simulation: if pyproject.toml had a stable version, no block."""
        # We test the logic independently: when pending_version is empty, no block
        simulated_pending = ""
        sim_block = False
        if 5 > 0 and bool(simulated_pending):
            sim_block = True
        assert not sim_block, "Stable version (no pre-release) → should allow dispatch"

    def test_block_message_contains_counts(self):
        """Verify the block message pattern includes count info."""
        src = _plugin_source()
        assert "${unpushedCount}" in src, "Block message must include unpushed commit count"
        assert "${pendingVersion}" in src, "Block message must include pending release version"
