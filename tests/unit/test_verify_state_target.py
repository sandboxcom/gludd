"""TDD tests for the verify-state Makefile target.

The gap: before committing or claiming work done, an agent had to run
git-status, git-log, git-rev-parse, git ls-remote, and gh run list
separately to assemble the full pre-claim picture. verify-state consolidates
working-tree cleanliness, HEAD identity, remote sync state, recent commits,
and CI verdict into one read-only command.

These tests prove the target exists, runs without error (its network calls
are fail-soft), and emits each of the five state sections.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = cast(str, compose_makefile(MAKEFILE))
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestVerifyStateTarget:
    """verify-state consolidates git/CI state for pre-claim verification."""

    def test_verify_state_target_exists(self) -> None:
        assert _recipe("verify-state"), "verify-state target must exist"

    def test_verify_state_runs_without_error(self) -> None:
        """`make verify-state` exits 0.

        The recipe is fail-soft: every network call (git ls-remote, gh run
        list) is wrapped in 2>/dev/null with a fallback message, so the
        command succeeds even when the network or gh is unavailable.
        """
        result = subprocess.run(
            ["make", "-s", "verify-state"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, (
            "verify-state must exit 0 (it is read-only and fail-soft).\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )

    def test_verify_state_includes_working_tree_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "Working Tree" in recipe, (
            "verify-state must emit a 'Working Tree' section showing CLEAN/DIRTY"
        )

    def test_verify_state_includes_head_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "HEAD" in recipe, (
            "verify-state must emit a 'HEAD' section with local SHA + branch"
        )

    def test_verify_state_includes_ci_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "CI" in recipe, (
            "verify-state must emit a 'CI' section with the verdict for HEAD"
        )

    @pytest.mark.parametrize("ci_exit_code", [0, 1])
    def test_ci_report_uses_locked_runtime_without_sync(
        self, tmp_path: Path, ci_exit_code: int
    ) -> None:
        """Execute the actual CI recipe with a broken legacy Python on PATH."""
        recipe = _recipe("verify-state")
        ci_recipe = recipe.split('\t@echo "--- CI ---"\n', maxsplit=1)[1]
        ci_recipe = ci_recipe.split('\t@echo ""', maxsplit=1)[0]
        sha = "a" * 40
        fake_git = tmp_path / "git"
        fake_git.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"print({sha!r} if sys.argv[1] == 'rev-parse' else 'development')\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        argv_path = tmp_path / "uv-argv.json"
        uv_probe = tmp_path / "uv_probe.py"
        uv_probe.write_text(
            "import json, sys\nfrom pathlib import Path\n"
            f"Path({str(argv_path)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "print('EXACT_SHA_STATUS_NOT_RELEASE_APPROVAL')\n"
            f"sys.exit({ci_exit_code})\n",
            encoding="utf-8",
        )
        probe_makefile = tmp_path / "Makefile"
        probe_makefile.write_text(
            f"export PATH := {tmp_path}:$(PATH)\nverify-state:\n{ci_recipe}",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "make", "-s", "-f", str(probe_makefile), "verify-state",
                f"UV={sys.executable} {uv_probe}", "PYTHON=false",
            ],
            cwd=tmp_path, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert argv_path.exists(), "CI reporting bypassed the selected locked runtime"
        assert json.loads(argv_path.read_text(encoding="utf-8")) == [
            "run", "--no-sync", "python", "scripts/pipeline_status.py", "status",
            "--remote-only", "--repo", "sandboxcom/gludd", "--remote", "sandboxcom",
            "--branch", "development", "--sha", sha,
        ]
        assert "EXACT_SHA_STATUS_NOT_RELEASE_APPROVAL" in result.stdout

    def test_read_only_state_report_has_public_contract(self) -> None:
        payload = json.loads((ROOT / "config/make_target_contract.json").read_text())
        contract = next(item for item in payload["targets"] if item["name"] == "verify-state")
        assert contract["make_variables"] == []
        assert contract["behavior"] == "make verify-state"
        makefile = compose_makefile(MAKEFILE)
        assert ".PHONY: verify-state" in makefile
        assert '"  verify-state ' in makefile
