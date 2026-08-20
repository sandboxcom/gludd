"""Validate Makefile syntax — catches TAB vs space errors and missing separators."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _make_exported_term(term: str) -> str:
    """Return TERM observed by a recipe under the project Makefile."""
    env = os.environ.copy()
    env["TERM"] = term
    result = subprocess.run(
        [
            "make",
            "-s",
            "-f",
            str(MAKEFILE),
            "-f",
            "-",
            "print-term",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        input='print-term:\n\t@printf "%s" "$$TERM"\n',
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_makefile_exports_suitable_term_default() -> None:
    """Non-interactive TERM=dumb is promoted for TUI-aware test subprocesses."""
    assert _make_exported_term("dumb") == "xterm-256color"


def test_makefile_preserves_suitable_caller_term() -> None:
    """A usable caller-selected terminal type must not be overwritten."""
    assert _make_exported_term("screen-256color") == "screen-256color"


def test_makefile_replaces_unavailable_caller_term() -> None:
    """An uninstalled terminal type must not leak warnings into test output."""
    assert _make_exported_term("gludd-no-such-terminal") == "xterm-256color"


def test_makefile_parses() -> None:
    """make -n on a no-op target must exit 0 (no syntax errors)."""
    result = subprocess.run(
        ["make", "-n", "-f", str(MAKEFILE), "help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Makefile has syntax errors:\n{result.stderr[-500:]}"
    )


def test_makefile_no_tabs_in_phony() -> None:
    """.PHONY continuation lines must use spaces, not tabs."""
    content = MAKEFILE.read_text()
    in_phony = False
    for i, line in enumerate(content.split("\n"), 1):
        if line.strip().startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if line.rstrip("\n").endswith("\\"):
                assert "\t" not in line.lstrip("\n"), (
                    f"Makefile:{i}: TAB in .PHONY continuation — use spaces only"
                )
            else:
                in_phony = False


def _parse_targets() -> tuple[dict[str, int], list[str], list[tuple[int, str]]]:
    """Return {target_name: line_number} for all non-.PHONY targets."""
    content = MAKEFILE.read_text()
    lines = content.split("\n")
    targets: dict[str, int] = {}
    in_phony = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if not line.rstrip("\n").endswith("\\"):
                in_phony = False
            continue
        if (re.match(r'^[a-zA-Z_][a-zA-Z0-9_./-]*\s*:(?!=)(?!\s*$).*', stripped)
                and not stripped.startswith(".")
                and not line.startswith((" ", "\t"))):
            name = stripped.split(":")[0].strip()
            targets[name] = i
    return targets, lines, list(enumerate(lines, 1))


def test_no_target_without_recipe() -> None:
    """Every non-PHONY target must have a tab-indented recipe or a dependency-only definition."""
    _, lines, _ = _parse_targets()
    in_phony = False
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if not lines[i - 1].rstrip("\n").endswith("\\"):
                in_phony = False
            continue
        if (re.match(r'^[a-zA-Z_][a-zA-Z0-9_./-]*\s*:(?!=)(?!\s*$).*', stripped)
                and not line.startswith((" ", "\t"))):
            if "$(" in stripped and not stripped.endswith("\\"):
                continue
            if i >= len(lines):
                violations.append(f"Makefile:{i}: target '{stripped}' at end of file, no recipe")
                continue
            next_line = lines[i]
            if next_line.startswith("\t"):
                continue
            if re.match(r'^[a-zA-Z_\.]', next_line.strip()):
                continue
            if next_line.strip() == "" or next_line.strip().startswith("#"):
                continue
            violations.append(
                f"Makefile:{i}: target '{stripped}' has no recipe or follow-on target "
                f"(next non-blank line: '{next_line.strip()[:60]}')"
            )
    assert not violations, (
        f"{len(violations)} target(s) without recipe:\n" + "\n".join(violations[:5])
    )


def test_blank_line_between_targets() -> None:
    """Target blocks should be separated by blank lines for readability."""
    _, lines, _ = _parse_targets()
    in_phony = False
    violations: list[str] = []
    in_target_block = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if not lines[i - 1].rstrip("\n").endswith("\\"):
                in_phony = False
            continue
        if (re.match(r'^[a-zA-Z_][a-zA-Z0-9_./-]*\s*:(?!=)(?!\s*$).*', stripped)
                and not line.startswith((" ", "\t"))):
            if in_target_block and not line.startswith("."):
                violations.append(f"Makefile:{i}: target '{stripped}' not preceded by blank line")
            in_target_block = True
            continue
        if stripped == "" or stripped.startswith("#"):
            in_target_block = False
    assert not violations, (
        f"{len(violations)} target(s) not preceded by blank line:\n" + "\n".join(violations[:5])
    )


def test_phony_targets_have_no_file() -> None:
    """Targets listed in .PHONY must not also be real file targets on disk."""
    content = MAKEFILE.read_text()
    phony_names: set[str] = set()
    in_phony = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            tokens = stripped.split(":", 1)[1].split()
            phony_names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            continue
        if in_phony:
            tokens = stripped.split()
            phony_names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            if not line.rstrip("\n").endswith("\\"):
                in_phony = False
    repo_root = MAKEFILE.parent
    violations: list[str] = []
    for name in sorted(phony_names):
        candidate = repo_root / name
        if candidate.exists():
            violations.append(f"PHONY target '{name}' exists as file: {candidate}")
    assert not violations, (
        f"{len(violations)} PHONY target(s) exist as files:\n" + "\n".join(violations[:5])
    )


_VAR_ASSIGN_LINE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*[+?]?=')


def test_variable_format() -> None:
    """Variable assignments should use := (simple-expanded) or ?= consistently."""
    content = MAKEFILE.read_text()
    lines = content.split("\n")
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            continue
        if _VAR_ASSIGN_LINE.match(stripped):
            if ":=" not in stripped and "?=" not in stripped and "=" in stripped and "+=" not in stripped:
                continue
            if "?=" in stripped or ":=" in stripped or "+=" in stripped:
                continue
            violations.append(f"Makefile:{i}: variable '{stripped[:60]}' uses bare '='")
    assert not violations, (
        f"{len(violations)} variable(s) use bare '=' instead of :=\n" + "\n".join(violations[:5])
    )


def test_no_trailing_whitespace() -> None:
    """No lines should have trailing spaces or tabs."""
    content = MAKEFILE.read_text()
    violations: list[str] = []
    for i, line in enumerate(content.split("\n"), 1):
        if line != line.rstrip():
            trailing = line[len(line.rstrip()):]
            if trailing == "\t":
                continue
            violations.append(
                f"Makefile:{i}: trailing whitespace "
                f"(len={len(trailing)}, repr={trailing!r})"
            )
    assert not violations, (
        f"{len(violations)} line(s) with trailing whitespace:\n" + "\n".join(violations[:5])
    )


def test_no_duplicate_targets() -> None:
    """FAIL if any non-PHONY target is defined more than once."""
    targets, _, _ = _parse_targets()
    seen: dict[str, int] = {}
    duplicates: dict[str, list[int]] = {}
    for name, line_no in sorted(targets.items()):
        if name in seen:
            duplicates.setdefault(name, [seen[name]]).append(line_no)
        else:
            seen[name] = line_no
    if duplicates:
        msg_lines = ["Makefile: duplicate target definitions found:"]
        for name, lines in sorted(duplicates.items()):
            msg_lines.append(f"  {name}: lines {', '.join(str(ln) for ln in lines)}")
        pytest.fail("\n".join(msg_lines))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
