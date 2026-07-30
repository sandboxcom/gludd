from __future__ import annotations

import os
import shutil
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


def test_search_target_is_portable_and_avoids_xargs_match_status() -> None:
    content = Path("Makefile").read_text()
    start = content.index("\nsearch:")
    end = content.index("\n\n", start)
    section = content[start:end]

    assert "command -v rg" in section
    assert "xargs -0 grep" not in section
    assert "/usr/bin/grep -R" in section


def test_search_target_succeeds_when_ripgrep_finds_match_before_transient_error(
    tmp_path: Path,
) -> None:
    fake_rg = tmp_path / "rg"
    fake_rg.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' './Makefile:5833:cat-file:'\n"
        "printf '%s\\n' 'rg: transient file disappeared' >&2\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    proc = subprocess.run(
        ["make", "search", "PATTERN=^cat-file:"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Makefile:5833:cat-file:" in proc.stdout


def test_search_fallback_succeeds_when_match_precedes_scan_error(
    tmp_path: Path,
) -> None:
    search_root = tmp_path / "search-root"
    search_root.mkdir()
    (search_root / "needle.txt").write_text("cat-file:\n", encoding="utf-8")
    (search_root / "vanished").symlink_to(search_root / "missing")
    unreadable = search_root / "unreadable.txt"
    unreadable.write_text("unreadable\n", encoding="utf-8")
    unreadable.chmod(0)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    make_bin = shutil.which("make")
    assert make_bin is not None
    safe_search_root = str(search_root).replace("/private/tmp/", "/tmp/", 1)

    proc = subprocess.run(
        [
            make_bin,
            "search",
            "PATTERN=^cat-file:",
            f"SEARCH_PATH={safe_search_root}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(os.environ, PATH=str(empty_bin)),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "needle.txt:1:cat-file:" in proc.stdout


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
