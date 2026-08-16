"""E2E tests for the real ``enforce-make.ts`` runtime hook.

The default suite invokes the actual TypeScript plugin through Gludd's Node
hook harness, so allow/deny behavior is offline, deterministic, and bounded.
Real OpenCode plugin-loader coverage lives in ``test_opencode_binary_boot.py``
and uses the supported ``opencode serve`` path; ``opencode run`` requires a
model session and has upstream non-termination reports, so it is not a safe
CI enforcement probe.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPENCODE_BIN = "opencode"

BLOCKED_PHRASES = [
    "BLOCKED",
    "not allowed",
    "Command does not start with",
    "Direct bash commands are not allowed",
]

_OPENCODE_MISSING = shutil.which(OPENCODE_BIN) is None or not Path(shutil.which(OPENCODE_BIN) or "").exists()
_CI_RUN = __import__("os").environ.get("CI") in ("1", "true")
pytestmark = pytest.mark.xdist_group("opencode-live")
_opencode_binary_skip = pytest.mark.skipif(
    _OPENCODE_MISSING or _CI_RUN,
    reason="opencode binary not resolvable in this environment (live-loader coverage requires a local install)",
)


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def _run_enforce_make(hook_env: HookEnv, command: str) -> subprocess.CompletedProcess[str]:
    """Invoke the source plugin's real pre-tool hook with a bash command."""
    return hook_env.invoke(
        "enforce-make.ts",
        "tool.execute.before",
        input={"tool": "bash", "args": {"command": command}},
        env_overrides={"GLUDD_REPO_ROOT": str(PROJECT_ROOT)},
    )


def _has_block_phrase(text: str) -> bool:
    """True if any BLOCKED_PHRASE appears (case-insensitive) in *text*."""
    lowered = text.lower()
    return any(p.lower() in lowered for p in BLOCKED_PHRASES)


# -- bounded binary discovery; loader boot is covered by the serve E2E ------


class TestOpencodeBinarySmoke:
    """Keep fast binary discovery separate from provider-backed model runs."""

    @_opencode_binary_skip
    def test_binary_reports_version(self):
        assert OPENCODE_BIN is not None
        result = subprocess.run(
            [OPENCODE_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert (result.stdout + result.stderr).strip()

    @_opencode_binary_skip
    def test_supported_server_command_is_available(self):
        assert OPENCODE_BIN is not None
        result = subprocess.run(
            [OPENCODE_BIN, "serve", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert (result.stdout + result.stderr).strip()


# -- enforce-make.ts runtime enforcement -------------------------------------


class TestOpencodeEnforceMake:
    """The loaded source plugin must block non-make bash commands."""

    def test_known_make_target_allowed(self, hook_plugin_env: HookEnv):
        # This assertion runs inside pytest, so a nested test target is correctly
        # rejected by the gate-concurrency guard.  Use a read-only Make target to
        # isolate the command-prefix contract under test.
        result = _run_enforce_make(hook_plugin_env, "make help")
        assert result.returncode == 0, result.stderr
        assert not _has_block_phrase(result.stdout + result.stderr)

    def test_python3_blocked(self, hook_plugin_env: HookEnv):
        result = _run_enforce_make(hook_plugin_env, "python3 -c 'print(1)'")
        assert result.returncode != 0
        assert _has_block_phrase(result.stdout + result.stderr)

    def test_gh_blocked(self, hook_plugin_env: HookEnv):
        result = _run_enforce_make(hook_plugin_env, "gh --version")
        assert result.returncode != 0
        assert _has_block_phrase(result.stdout + result.stderr)

    def test_cat_blocked(self, hook_plugin_env: HookEnv):
        result = _run_enforce_make(hook_plugin_env, "cat /etc/hosts")
        assert result.returncode != 0
        assert _has_block_phrase(result.stdout + result.stderr)
