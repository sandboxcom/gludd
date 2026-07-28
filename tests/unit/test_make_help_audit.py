from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import check_make_help


def test_public_targets_are_listed_in_help() -> None:
    makefile = Path("Makefile")
    public = set(check_make_help.public_targets(makefile))
    listed = (
        check_make_help.help_targets_from_makefile(makefile)
        | check_make_help.help_targets_from_output()
    )

    assert public <= listed


def test_internal_targets_are_excluded_from_public_audit() -> None:
    makefile = Path("Makefile")
    public = set(check_make_help.public_targets(makefile))

    assert "_gate-fresh-check" not in public
    assert "commit-bootstrap" not in public


def test_search_target_defaults_to_workspace_not_shell_path() -> None:
    proc = subprocess.run(
        ["make", "search", "PATTERN=^cat-file:"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Makefile:" in proc.stdout


def test_codex_system_skill_read_uses_explicit_root(tmp_path: Path) -> None:
    skill_file = tmp_path / ".system" / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("demo skill\n", encoding="utf-8")

    proc = subprocess.run(
        [
            "make",
            "codex-system-skill-read",
            "SKILL=demo-skill",
            f"CODEX_SKILLS_ROOT={tmp_path}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout == "demo skill\n"


def test_codex_system_skill_read_rejects_traversal(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "make",
            "codex-system-skill-read",
            "SKILL=../skill-creator",
            f"CODEX_SKILLS_ROOT={tmp_path}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode != 0
    assert "Invalid skill name" in proc.stdout
