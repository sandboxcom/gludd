from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts import check_make_help
from scripts.makefile_layout import compose_makefile

REPO_ROOT = Path(__file__).resolve().parents[2]


def _search_target_block() -> str:
    content = compose_makefile(REPO_ROOT / "Makefile")
    start_marker = "export GLUDD_SEARCH_PATTERN := $(value PATTERN)\n"
    end_marker = "\nshow-lines:\n"
    assert content.count(start_marker) == 1
    assert content.count(end_marker) == 1
    start = content.index(start_marker)
    end = content.index(end_marker, start)
    block = content[start:end]
    assert block.splitlines()[:4] == [
        "export GLUDD_SEARCH_PATTERN := $(value PATTERN)",
        "export GLUDD_SEARCH_PATH := $(value SEARCH_PATH)",
        "unexport PATTERN SEARCH_PATH",
        "search:",
    ]
    return block


def _default_search_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, str], Path]:
    workspace = tmp_path / "workspace"
    (workspace / "make").mkdir(parents=True)
    (workspace / "make" / "workspace-only.mk").write_text(
        "cat-file:\n", encoding="utf-8",
    )
    path_bin = tmp_path / "path-bin"
    path_bin.mkdir()
    (path_bin / "outside-only.mk").write_text("cat-file:\n", encoding="utf-8")
    makefile = tmp_path / "search.mk"
    makefile.write_text(_search_target_block(), encoding="utf-8")
    env = dict(os.environ, PATH=f"{path_bin}{os.pathsep}{os.environ['PATH']}")
    env.pop("SEARCH_PATH", None)
    env.pop("GLUDD_SEARCH_PATH", None)
    env.pop("GLUDD_SEARCH_PATTERN", None)
    return workspace, makefile, env, path_bin


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


def test_search_target_defaults_to_workspace_not_shell_path(tmp_path: Path) -> None:
    workspace, makefile, env, _path_bin = _default_search_fixture(tmp_path)
    with patch.object(subprocess, "run", wraps=subprocess.run) as launch:
        proc = subprocess.run(
            ["make", "--no-print-directory", "-f", str(makefile), "search", "PATTERN=^cat-file:"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=workspace,
            env=env,
        )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "make/" in proc.stdout and ".mk:" in proc.stdout
    assert launch.call_count == 1
    assert launch.call_args is not None
    assert launch.call_args.kwargs.get("cwd") == workspace
    assert launch.call_args.args == (
        ["make", "--no-print-directory", "-f", str(makefile), "search", "PATTERN=^cat-file:"],
    )
    assert launch.call_args.kwargs == {
        "capture_output": True, "text": True, "timeout": 15, "cwd": workspace, "env": env,
    }
    assert "workspace-only.mk:1:cat-file:" in proc.stdout
    assert "outside-only.mk" not in proc.stdout


def test_search_target_default_remains_owned_with_environment_precedence(
    tmp_path: Path,
) -> None:
    workspace, makefile, env, _path_bin = _default_search_fixture(tmp_path)
    proc = subprocess.run(
        ["make", "-e", "--no-print-directory", "-f", str(makefile), "search", "PATTERN=^cat-file:"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "workspace-only.mk:1:cat-file:" in proc.stdout
    assert "outside-only.mk" not in proc.stdout


@pytest.mark.parametrize("search_make_options", [(), ("-e",)], ids=["ordinary", "environment"])
@pytest.mark.parametrize("search_default_mutation", ["shell-path", "outside-workspace"])
def test_search_default_scope_guard_detects_native_recipe_mutation(
    tmp_path: Path,
    search_make_options: tuple[str, ...],
    search_default_mutation: str,
) -> None:
    workspace, makefile, env, path_bin = _default_search_fixture(tmp_path)
    source = makefile.read_text(encoding="utf-8")
    original_default = "$${GLUDD_SEARCH_PATH:-.}"
    assert source.count(original_default) == 1
    if search_default_mutation == "shell-path":
        changed_default = "$${GLUDD_SEARCH_PATH:-$$PATH}"
    else:
        portable_path = str(path_bin).replace("/private/tmp/", "/tmp/", 1)
        changed_default = "$${GLUDD_SEARCH_PATH:-" + portable_path + "}"
    makefile.write_text(source.replace(original_default, changed_default), encoding="utf-8")
    proc = subprocess.run(
        [
            "make", *search_make_options, "--no-print-directory", "-f", str(makefile),
            "search", "PATTERN=^cat-file:",
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if search_default_mutation == "shell-path":
        assert proc.returncode != 0, proc.stderr + proc.stdout
        assert "workspace-only.mk" not in proc.stdout
    else:
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "outside-only.mk:1:cat-file:" in proc.stdout
        assert "workspace-only.mk" not in proc.stdout
        assert not ("make/" in proc.stdout and ".mk:" in proc.stdout)


def test_search_target_is_portable_and_avoids_xargs_match_status() -> None:
    content = compose_makefile(Path("Makefile"))
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
    assert "rg: transient file disappeared" in proc.stderr
    assert "SEARCH-PARTIAL matches_found=true scan_exit=2 complete=false" in proc.stderr


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
    assert "SEARCH-PARTIAL matches_found=true scan_exit=2 complete=false" in proc.stderr


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
