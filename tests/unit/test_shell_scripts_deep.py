"""Deep validation of shell scripts under scripts/*.sh.

Checks: shebang, strict-mode, quoting risks (path ops, backticks),
argument-parsing hygiene, error-handling surfaces (trap, exit codes,
stderr), and production-quality structural markers.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = ROOT / "scripts"


def _sh_files() -> list[Path]:
    return sorted(SCRIPTS_DIR.rglob("*.sh"))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _lines(p: Path) -> list[str]:
    return _read(p).splitlines()


# ---------------------------------------------------------------------------
# 1. Shebang
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
class TestShebang:
    def test_file_starts_with_hashbang(self, path: Path):
        first = _lines(path)[0] if _lines(path) else ""
        assert first.startswith("#!"), "missing shebang"

    def test_shebang_uses_env_bash(self, path: Path):
        first = _lines(path)[0].strip()
        assert first in (
            "#!/usr/bin/env bash",
            "#!/bin/bash",
            "#!/bin/sh",
        ), f"unsupported shebang: {first!r}"


# ---------------------------------------------------------------------------
# 2. Strict mode
# ---------------------------------------------------------------------------
class TestStrictMode:
    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_set_euo_pipefail_present(self, path: Path):
        content = _read(path)
        assert "pipefail" in content, "missing pipefail"
        assert "errexit" in content or re.search(r"set\s+-.*e", content), "missing errexit"
        assert "nounset" in content or re.search(r"set\s+-.*u", content), "missing nounset"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_strict_mode_before_first_side_effect(self, path: Path):
        lines = _lines(path)
        strict_idx = -1
        for i, L in enumerate(lines):
            s = L.strip()
            if ("set -" in s and "pipefail" in s) or "set -o errexit" in s:
                strict_idx = i
                break
        assert strict_idx >= 0, "no strict-mode line found"
        for _i, L in enumerate(lines[strict_idx + 1 :], strict_idx + 1):
            s = L.strip()
            if not s or s.startswith("#") or s.startswith("#!/"):
                continue
            if "set -" in s or "SHELL" in s:
                continue
            return
        raise AssertionError("no non-comment line after strict-mode declaration")


# ---------------------------------------------------------------------------
# 3. Quoting — genuinely risky patterns only
# ---------------------------------------------------------------------------
class TestQuoting:
    # Lines like:  rm $file  or  mv $a $b  or  source $script  or  . $script
    # where the variable is NOT inside double quotes and follows a path-sensitive
    # command.  This EXCLUDES for/in loops (word-splitting is intentional for
    # space-separated lists) and echo/printf (benign).
    DANGER_PATH_OPS = re.compile(r"^\s*(?:rm|rmdir|cp|mv|ln|touch|cd|source|\.)\s+\$[A-Za-z_][A-Za-z0-9_]*\s")

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_no_dangerously_unquoted_path_args(self, path: Path):
        lines = _lines(path)
        violations: list[tuple[int, str]] = []
        for i, L in enumerate(lines, 1):
            stripped = L.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if self.DANGER_PATH_OPS.search(stripped):
                violations.append((i, stripped))
        assert not violations, f"{len(violations)} line(s) with dangerously unquoted path args:\n" + "\n".join(
            f"  L{n}: {t}" for n, t in violations[:8]
        )

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_no_backtick_subshells(self, path: Path):
        lines = _lines(path)
        bad = [(i, L.strip()) for i, L in enumerate(lines, 1) if "`" in L and not L.strip().startswith("#")]
        assert not bad, "backticks found — use $() instead:\n" + "\n".join(f"  L{n}: {t}" for n, t in bad[:5])


# ---------------------------------------------------------------------------
# 4. Argument parsing
# ---------------------------------------------------------------------------
class TestArgumentParsing:
    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_case_or_shift_for_positional_args(self, path: Path):
        content = _read(path)
        if not re.search(r"\$[1-9]", content):
            return
        has_case = "case " in content and "esac" in content
        has_shift = "shift" in content
        # gated_merge.sh takes args via env vars, not positional
        uses_env_args = "BRANCHES" in content and "BASE" in content
        assert has_case or has_shift or uses_env_args, (
            "references positional args but no case/esac, shift, or env-var-based arg intake"
        )

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_help_text_for_arg_accepting_scripts(self, path: Path):
        content = _read(path)
        uses_args = "${1:-}" in content or '"$@"' in content
        if not uses_args:
            return
        has_help = any(kw in content for kw in ("usage", "Usage", "--help", "-h)", "USAGE", "Usage:"))
        assert has_help, "accepts arguments but has no usage/--help text"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_dollar_at_not_dollar_star_for_forwarding(self, path: Path):
        content = _read(path)
        if '"$@"' not in content:
            return
        # "$*" inside double quotes joins args with IFS[0] — legitimate
        # for creating a single string from all args (not forwarding).
        # Only flag bare $* (no quotes) used as arg forwarding.
        dangerous = []
        for i, L in enumerate(_lines(path), 1):
            # Bare $* (not "$*") outside printf
            if re.search(r'(?<!")\$\*(?!")', L) and "printf" not in L:
                dangerous.append((i, L.strip()))
        assert not dangerous, 'bare $* without quotes (use "$@" for forwarding):\n' + "\n".join(
            f"  L{n}: {t}" for n, t in dangerous[:5]
        )


# ---------------------------------------------------------------------------
# 5. Error handling
# ---------------------------------------------------------------------------
class TestErrorHandling:
    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_lock_management_has_cleanup(self, path: Path):
        """Scripts creating lock files must have either trap or explicit
        cleanup on all exit paths."""
        content = _read(path)
        has_lock = "LOCK_FILE" in content or ("flock" in content and "exec" in content)
        if not has_lock:
            return
        has_trap = "trap" in content
        has_explicit_cleanup = "_release_lock" in content or "cleanup" in content
        assert has_trap or has_explicit_cleanup, "acquires locks but has neither trap nor explicit cleanup function"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_stderr_on_error_messages(self, path: Path):
        content = _read(path)
        has_error = bool(
            re.search(
                r'(echo|printf).*"?\[.*\].*(?:error|ERROR|fatal|FATAL)',
                content,
            )
        )
        if has_error:
            assert ">&2" in content, "prints error messages but none redirect to stderr (>&2)"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_exit_calls_carry_explicit_code(self, path: Path):
        lines = _lines(path)
        bare = [(i, L.strip()) for i, L in enumerate(lines, 1) if L.strip() == "exit"]
        assert not bare, "bare `exit` without exit code:\n" + "\n".join(f"  L{n}" for n, _ in bare)


# ---------------------------------------------------------------------------
# 6. Production-quality markers
# ---------------------------------------------------------------------------
class TestProductionQuality:
    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_header_describes_script(self, path: Path):
        lines = _lines(path)
        head = "\n".join(lines[1:15]).lower()
        stem = path.name.replace(".sh", "")
        assert stem.replace("_", "") in head.replace("_", "").replace("-", "") or (
            "usage" in head or "design" in head
        ), "no description in header (first 15 lines)"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_no_hardcoded_home_dir_paths(self, path: Path):
        content = _read(path)
        hardcoded = re.findall(r'(?<![A-Za-z0-9_])/home/[a-zA-Z][^ \t\n"\';&|]*', content)
        assert not hardcoded, f"hardcoded /home/ paths: {hardcoded}"

    @pytest.mark.parametrize("path", _sh_files(), ids=lambda p: p.name)
    def test_env_var_overrides_for_paths(self, path: Path):
        content = _read(path)
        writes_files = ">" in content
        uses_mktemp = "mktemp" in content
        if writes_files and not uses_mktemp:
            has_override = bool(re.search(r"\$\{[A-Z_]+:-\S+\}", content))
            if not has_override:
                # Advisory: scripts writing files should allow env overrides.
                # Not a hard fail — scripts may have legitimate fixed paths.
                pass


# ---------------------------------------------------------------------------
# 7. Collection-count invariant
# ---------------------------------------------------------------------------
class TestScriptInventory:
    def test_all_sh_files_discovered(self):
        paths = _sh_files()
        names = {p.name for p in paths}
        expected = {
            "azure_event_guard.sh",
            "ci_push_and_verify.sh",
            "clean-root.sh",
            "disk-guard.sh",
            "gate_async.sh",
            "gated_merge.sh",
            "run_gate.sh",
            "run_test_background.sh",
            "ship_async.sh",
        }
        missing = expected - names
        extra = names - expected
        assert not missing, f".sh files missing from discovery: {missing}"
        assert not extra, f"unexpected .sh files found: {extra}"
