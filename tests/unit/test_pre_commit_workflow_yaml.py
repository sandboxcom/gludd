"""Tests for the pre-commit hook that validates .github/workflows/build.yml.

Verifies RP.15: the hook script exists, is executable, and exits non-zero
on the classes of YAML breakage that historically caused CI failures
(unquoted !cancelled() tags, malformed indentation, etc.).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "scripts" / "hooks" / "pre-commit-workflow-yaml"


def _run_hook(yml_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the hook script. If yml_path is given, pass it as the path arg."""
    argv: list[str] = [str(HOOK_PATH)]
    if yml_path is not None:
        argv.append(str(yml_path))
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )


@pytest.fixture
def malformed_yml(tmp_path: Path) -> Path:
    """A YAML file with bad indentation that safe_load rejects."""
    p = tmp_path / "bad.yml"
    p.write_text(
        "name: Bad\n"
        "on:\n"
        "  push:\n"
        "    branches:\n"
        "      - master\n"
        "   jobs:\n"  # wrong indent -> YAML parse error
        "      foo:\n"
        "        runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def unquoted_bang_cancelled_yml(tmp_path: Path) -> Path:
    """A YAML file with an unquoted !cancelled() tag.

    GitHub Actions interprets `!cancelled()` as a YAML tag, which fails at
    parse time. PyYAML's safe_load also rejects unknown tags.
    """
    p = tmp_path / "bang.yml"
    p.write_text(
        'name: Bang\n'
        'on:\n'
        '  push:\n'
        '    branches: [master]\n'
        'jobs:\n'
        '  foo:\n'
        '    runs-on: ubuntu-latest\n'
        '    if: failure() && !cancelled()\n',
        encoding="utf-8",
    )
    return p


@pytest.fixture
def valid_yml(tmp_path: Path) -> Path:
    """A minimal valid workflow YAML that both layers accept."""
    p = tmp_path / "ok.yml"
    p.write_text(
        'name: OK\n'
        'on:\n'
        '  push:\n'
        '    branches: [master]\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - run: echo hello\n',
        encoding="utf-8",
    )
    return p


class TestHookScriptProperties:
    """Static properties of the hook script itself."""

    def test_hook_script_exists(self):
        assert HOOK_PATH.exists(), f"hook script missing at {HOOK_PATH}"

    def test_hook_script_is_executable(self):
        assert HOOK_PATH.is_file(), f"hook script missing at {HOOK_PATH}"
        mode = HOOK_PATH.stat().st_mode
        assert mode & 0o111, f"hook script is not executable: {oct(mode)}"

    def test_hook_script_has_shebang(self):
        assert HOOK_PATH.is_file(), f"hook script missing at {HOOK_PATH}"
        first = HOOK_PATH.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#!"), f"missing shebang: {first!r}"


class TestHookBehaviorOnMalformedYaml:
    """The hook MUST exit non-zero on broken YAML."""

    def test_exits_nonzero_on_bad_indentation(self, malformed_yml: Path):
        result = _run_hook(malformed_yml)
        assert result.returncode != 0, (
            f"expected non-zero exit on malformed YAML, got 0. "
            f"stderr={result.stderr}"
        )

    def test_exits_nonzero_on_unquoted_bang_cancelled(
        self, unquoted_bang_cancelled_yml: Path
    ):
        result = _run_hook(unquoted_bang_cancelled_yml)
        assert result.returncode != 0, (
            f"expected non-zero exit on unquoted !cancelled(), got 0. "
            f"stderr={result.stderr}"
        )

    def test_stderr_mentions_parse_error_on_malformed(self, malformed_yml: Path):
        result = _run_hook(malformed_yml)
        combined = (result.stdout + result.stderr).lower()
        assert "error" in combined or "fail" in combined, (
            f"expected an error/fail mention in output, got: {combined}"
        )


class TestHookBehaviorOnValidYaml:
    """The hook MUST exit zero on well-formed YAML."""

    def test_exits_zero_on_valid_yml(self, valid_yml: Path):
        result = _run_hook(valid_yml)
        assert result.returncode == 0, (
            f"expected zero exit on valid YAML, got {result.returncode}. "
            f"stdout={result.stdout} stderr={result.stderr}"
        )

    def test_exits_zero_on_canonical_build_yml(self):
        """The real .github/workflows/build.yml MUST pass the hook."""
        canonical = ROOT / ".github" / "workflows" / "build.yml"
        assert canonical.is_file(), "Required canonical build.yml is missing"
        result = _run_hook(canonical)
        assert result.returncode == 0, (
            f"canonical build.yml failed the hook: rc={result.returncode}, "
            f"stdout={result.stdout}, stderr={result.stderr}"
        )


class TestHookSkipsGracefullyWhenMissing:
    """When the target file does not exist, the hook SKIPs (exit 0)."""

    def test_skip_exit_zero_on_nonexistent_file(self, tmp_path: Path):
        result = _run_hook(tmp_path / "does-not-exist.yml")
        assert result.returncode == 0, (
            f"expected exit 0 on missing file, got {result.returncode}"
        )


class TestMakefileInstallTarget:
    """The Makefile MUST expose install-workflow-hook."""

    def test_install_workflow_hook_target_exists(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert "install-workflow-hook:" in makefile, (
            "Makefile missing 'install-workflow-hook:' target"
        )

    def test_install_workflow_hook_target_copies_hook(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        # The target should reference the hook script path.
        assert "pre-commit-workflow-yaml" in makefile, (
            "install-workflow-hook target does not reference the hook script"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
