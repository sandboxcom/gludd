"""BP.8: Verify pre-commit lint hook is installed, executable, and functional.

Tests that ``scripts/hooks/pre-commit-lint``:
  - exists at the expected path
  - is executable
  - has a valid bash shebang
  - references ``make lint``
  - exits non-zero when lint fails (functional mock test)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parent.parent.parent
_HOOK = _PROJECT / "scripts" / "hooks" / "pre-commit-lint"


class TestPreCommitLintHookInstalled:
    def test_hook_script_exists(self) -> None:
        """Script exists at scripts/hooks/pre-commit-lint."""
        assert _HOOK.is_file(), f"pre-commit-lint hook not found at {_HOOK}"

    def test_hook_script_is_executable(self) -> None:
        """Script is executable (os.access X_OK)."""
        if not _HOOK.is_file():
            pytest.fail(f"hook not found at {_HOOK}")
        assert os.access(_HOOK, os.X_OK), "pre-commit-lint is not executable (X_OK)"

    @pytest.mark.parametrize(
        "shebang",
        ["#!/bin/bash", "#!/usr/bin/env bash"],
    )
    def test_hook_script_has_valid_shebang(self, shebang: str) -> None:
        """Script has a valid shebang (#!/bin/bash or #!/usr/bin/env bash)."""
        if not _HOOK.is_file():
            pytest.fail(f"hook not found at {_HOOK}")
        first_line = _HOOK.read_text(encoding="utf-8").splitlines()[0]
        # The parametrized shebang must appear as the first line. We accept
        # either form; at least one of the two parametrized cases must match,
        # so we assert that the first line equals one of the two known forms.
        assert first_line in ("#!/bin/bash", "#!/usr/bin/env bash"), (
            f"invalid shebang: {first_line!r} (expected #!/bin/bash or #!/usr/bin/env bash)"
        )

    def test_hook_script_references_make_lint(self) -> None:
        """Script references `make lint`."""
        if not _HOOK.is_file():
            pytest.fail(f"hook not found at {_HOOK}")
        content = _HOOK.read_text(encoding="utf-8")
        assert "make lint" in content, "hook script does not invoke 'make lint'"

    def test_hook_exits_nonzero_when_lint_fails(self, tmp_path: Path) -> None:
        """Functional mock test: script exits non-zero when `make lint` fails.

        Sets up a mock repo layout (scripts/hooks/pre-commit-lint + a Makefile
        whose ``lint`` target exits 1) in a temp directory, runs the hook, and
        asserts the exit code is non-zero and an error message is printed.
        """
        if not shutil.which("make"):
            pytest.skip("make not available on PATH")
        if not shutil.which("bash"):
            pytest.skip("bash not available on PATH")

        # Mirror the real repo layout so the script's relative Makefile check
        # resolves correctly: <tmp>/scripts/hooks/pre-commit-lint + <tmp>/Makefile
        hooks_dir = tmp_path / "scripts" / "hooks"
        hooks_dir.mkdir(parents=True)
        script_copy = hooks_dir / "pre-commit-lint"
        script_copy.write_text(_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(script_copy, 0o755)

        # Mock Makefile: lint target fails immediately.
        makefile = tmp_path / "Makefile"
        makefile.write_text("lint:\n\texit 1\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(script_copy)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0, (
            f"script should exit non-zero when lint fails (got {result.returncode})"
        )
        combined = (result.stdout + result.stderr).lower()
        assert "fail" in combined or "error" in combined, (
            f"script should print an error message on failure; "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    def test_hook_exits_nonzero_when_makefile_missing(self, tmp_path: Path) -> None:
        """Script exits non-zero when the Makefile is not found."""
        if not shutil.which("bash"):
            pytest.skip("bash not available on PATH")

        hooks_dir = tmp_path / "scripts" / "hooks"
        hooks_dir.mkdir(parents=True)
        script_copy = hooks_dir / "pre-commit-lint"
        script_copy.write_text(_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(script_copy, 0o755)

        # No Makefile in tmp_path — the script should detect this and exit 1.
        result = subprocess.run(
            ["bash", str(script_copy)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0, (
            f"script should exit non-zero when Makefile is missing "
            f"(got {result.returncode})"
        )
        assert "error" in result.stderr.lower() or "not found" in result.stderr.lower(), (
            f"script should print an error about the missing Makefile; "
            f"stderr={result.stderr!r}"
        )
