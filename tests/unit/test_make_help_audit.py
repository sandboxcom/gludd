from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import check_make_help, check_make_target_contract, edit_makefile_target
from scripts.makefile_layout import compose_makefile

from tests.unit import test_makefile_audit_deep as deep_audit


def _native_help_fixture(
    tmp_path: Path, *, index_status: int = 0, literal: str | None = None, target: str = "help",
    edit_section: str | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], list[str]]:
    """Execute the real help recipe with a bounded index/shell observation seam."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "make/00-foundation.mk").read_text()
    recipe = source.split("\nhelp:\n", 1)[1].split("\nsdd-constitution:", 1)[0]
    if literal is not None:
        recipe = recipe.replace('"Usage: make [target]"', shlex.quote(literal).replace("$", "$$"), 1)
    index = tmp_path / "native-index"
    index.write_text(
        "import sys\n"
        "assert sys.argv[1:] == ['-m', 'scripts.check_make_help', '--print-index']\n"
        "print('  native-index-contract available', flush=True)\n"
        f"raise SystemExit({index_status})\n"
    )
    trace = tmp_path / "owned-shell-calls"
    shell = tmp_path / "owned-shell"
    shell.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' invocation >> {shlex.quote(str(trace))}\n"
        'exec /bin/sh "$@"\n'
    )
    shell.chmod(0o700)
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f"SHELL := {shell}\nPYTHON := {shlex.quote(sys.executable)} {shlex.quote(str(index))}\n"
        "help:\n" + recipe + "\nquiet:\n\t@:\n\n# --- Setup ---\nbase-fixture:\n\t@:\n"
    )
    environment = dict(os.environ)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"):
        environment.pop(name, None)
    if edit_section is not None:
        edited = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; from pathlib import Path; "
                "from scripts import edit_makefile_target as editor; "
                "editor.MAKEFILE_PATH = Path(sys.argv[1]); "
                "sys.argv = ['edit-makefile-target', 'add', '--name', 'help-roundtrip', "
                "'--description', 'Retained native editor', '--section', sys.argv[2]]; "
                "raise SystemExit(editor.main())",
                str(makefile), edit_section,
            ],
            cwd=root, env=environment, capture_output=True, check=False, timeout=15,
        )
        assert edited.returncode == 0, edited.stdout + edited.stderr
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(makefile), target], cwd=tmp_path,
        env=environment, capture_output=True, check=False, timeout=15,
    )
    return result, trace.read_text().splitlines()


def test_help_recipe_uses_one_shell_for_all_static_text_and_native_index(tmp_path: Path) -> None:
    result, calls = _native_help_fixture(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert calls == ["invocation"]
    assert result.stdout.startswith(b"Usage: make [target]\n\n  --- Setup ---\n")
    assert result.stdout.endswith(b"  foo-test                Test\n\n\n")
    assert result.stdout.index(b"  --- Complete Target Index ---\n") < result.stdout.index(
        b"  native-index-contract available\n"
    ) < result.stdout.index(b"  --- New Targets ---\n")


def test_help_prints_literal_text_without_format_or_shell_interpretation(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-run"
    literal = "Usage: %s 'quoted' \"double\" — " + "$(touch " + str(marker) + ") ${HOME} `touch " + str(marker) + "`"
    result, _ = _native_help_fixture(tmp_path, literal=literal)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == literal.encode()
    assert not marker.exists()


def test_help_index_failure_stays_visible_and_stops_before_footer(tmp_path: Path) -> None:
    result, _ = _native_help_fixture(tmp_path, index_status=17)
    assert result.returncode != 0
    assert b"Error 17" in result.stderr
    assert b"  native-index-contract available\n" in result.stdout
    assert b"  --- New Targets ---\n" not in result.stdout


def test_non_help_target_does_not_emit_help_during_make_expansion(tmp_path: Path) -> None:
    result, calls = _native_help_fixture(tmp_path, target="quiet")
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert calls == ["invocation"]


@pytest.mark.parametrize("section,index_status", [("Setup", 0), ("New Targets", 0), ("New Targets", 17)])
def test_native_help_editor_roundtrip_keeps_continuation_and_index_failure(
    tmp_path: Path, section: str, index_status: int,
) -> None:
    result, calls = _native_help_fixture(tmp_path, edit_section=section, index_status=index_status)
    assert calls == ["invocation"]
    assert b"  native-index-contract available\n" in result.stdout
    if index_status:
        assert result.returncode != 0
        assert b"Error 17" in result.stderr
        assert b"help-roundtrip" not in result.stdout
        assert b"  --- New Targets ---\n" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert b"  help-roundtrip          Retained native editor\n" in result.stdout
        assert result.stdout.endswith(b"  foo-test                Test\n\n\n")


@pytest.mark.parametrize("style", ["echo", "printf"])
def test_make_target_contract_recognizes_original_and_native_help(
    tmp_path: Path, style: str,
) -> None:
    value = '"  alpha INPUT=value  Native example"'
    recipe = "\t@echo " + value if style == "echo" else "\t@printf '%s\\n' \\\n\t\t" + value
    makefile = tmp_path / "Makefile"
    makefile.write_text("help:\n" + recipe + "\nalpha:\n\t@echo $(INPUT)\n")
    contract = {"targets": [{"name": "alpha", "make_variables": ["INPUT"], "behavior": "make alpha INPUT=value"}]}
    assert check_make_target_contract.validate_contract(makefile, contract) == []


@pytest.mark.parametrize(
    "mode,target,refuse,expected,message",
    [
        ("add", "original", False, 1, "already exists"),
        ("add", "new-target", True, 1, "Original Makefile unchanged"),
        ("extract", "original", False, 0, "original:"),
        ("extract", "missing", False, 1, "not found"),
        ("validate", "original", False, 0, ""),
        ("validate", "original", True, 1, ""),
        ("replace", "original", False, 0, "replaced and validated"),
        ("replace", "original", True, 1, "Original Makefile unchanged"),
        ("replace", "missing", False, 1, "not found"),
    ],
)
def test_native_help_editor_keeps_legacy_cli_exits_and_atomic_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    mode: str, target: str, refuse: bool, expected: int, message: str,
) -> None:
    makefile = tmp_path / "Makefile"
    original = b'original:\n\t@echo original\n\nhelp:\n\t@echo "Usage"\n'
    makefile.write_bytes(original)
    replacement = tmp_path / "replacement.mk"
    replacement.write_text("original:\n\t@echo replaced\n")
    monkeypatch.setattr(edit_makefile_target, "MAKEFILE_PATH", makefile)
    monkeypatch.setattr(edit_makefile_target, "_validate_tempfile", lambda path, name: not refuse)
    args = ["edit-makefile-target", mode, "--name", target]
    if mode == "add":
        args.extend(["--description", "Native example"])
    elif mode == "replace":
        args.extend(["--file", str(replacement)])
    monkeypatch.setattr(sys, "argv", args)

    assert edit_makefile_target.main() == expected
    captured = capsys.readouterr()
    assert message in captured.out + captured.err
    if mode == "replace" and not refuse and expected == 0:
        assert b"@echo replaced" in makefile.read_bytes()
    else:
        assert makefile.read_bytes() == original
    assert list(tmp_path.glob("Makefile.*.tmp")) == []


@pytest.mark.parametrize("style", ["echo", "printf"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_duplicate_help_audit_is_nonvacuous_for_old_and_native_printf_dry_runs(
    monkeypatch: pytest.MonkeyPatch, style: str, duplicate: bool,
) -> None:
    values = ["  alpha  First", "  beta  Second", "  alpha  Duplicate" if duplicate else "  omega  Last"]
    if style == "echo":
        output = "\n".join("echo " + shlex.quote(value) for value in values) + "\n"
    else:
        output = "printf '%s\\n' " + " \\\n  ".join(shlex.quote(value) for value in values) + "\n"

    def native_dry_run(
        command: list[str], *, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["make", "-n", "-f", str(deep_audit.MAKEFILE), "help"]
        assert capture_output and text and timeout == 30
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", native_dry_run)
    audit = deep_audit.TestHelpNoDupTargets()
    assert audit._help_lines() == ["alpha", "beta", "alpha" if duplicate else "omega"]
    if duplicate:
        with pytest.raises(AssertionError, match="duplicate help target"):
            audit.test_no_duplicate_help_entries()
    else:
        audit.test_no_duplicate_help_entries()


@pytest.mark.parametrize("status,output", [(0, ""), (17, 'echo "  alpha  First"\n')])
def test_duplicate_help_audit_refuses_empty_or_failed_native_dry_run(
    monkeypatch: pytest.MonkeyPatch, status: int, output: str,
) -> None:
    def native_dry_run(
        command: list[str], *, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and timeout == 30
        return subprocess.CompletedProcess(command, status, stdout=output, stderr="native failure" if status else "")

    monkeypatch.setattr(subprocess, "run", native_dry_run)
    with pytest.raises(AssertionError):
        deep_audit.TestHelpNoDupTargets._help_lines()


@pytest.mark.parametrize("style", ["echo", "printf"])
def test_static_help_inventory_recognizes_echo_and_native_printf(
    tmp_path: Path, style: str,
) -> None:
    values = ['"  alpha  First"', '"  beta  Second"', '"  omega  Last"']
    if style == "echo":
        recipe = "\n".join("\t@echo " + value for value in values)
    else:
        recipe = "\t@printf '%s\\n' \\\n" + " \\\n".join("\t\t" + value for value in values)
    makefile = tmp_path / "Makefile"
    makefile.write_text("help:\n" + recipe + "\n")

    assert check_make_help.help_targets_from_makefile(makefile) == {"alpha", "beta", "omega"}


@pytest.mark.parametrize("style", ["echo", "printf"])
def test_major_help_inventory_recognizes_echo_and_native_printf(
    monkeypatch: pytest.MonkeyPatch, style: str,
) -> None:
    values = ['"  alpha  First"', '"  beta  Second"', '"  omega  Last"']
    if style == "echo":
        output = "\n".join("echo " + value for value in values) + "\n"
    else:
        output = "printf '%s\\n' \\\n" + " \\\n".join("  " + value for value in values) + "\n"

    def native_dry_run(
        command: list[str], *, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["make", "-n", "-f", str(deep_audit.MAKEFILE), "help"]
        assert capture_output and text and timeout == 30
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", native_dry_run)
    names, original = deep_audit._help_target_names()
    assert names == {"alpha", "beta", "omega"}
    assert original == output


@pytest.mark.parametrize("mode", ["index", "complete", "missing"])
def test_native_help_audit_keeps_index_and_missing_target_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    (tmp_path / "Makefile").write_text(
        "alpha:\n\t@echo alpha\nbeta:\n\t@echo beta\n_internal:\n"
        '\t@echo "  alpha  First"\n'
    )
    monkeypatch.setattr(check_make_help, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        check_make_help, "help_targets_from_output", lambda: {"beta"} if mode == "complete" else set(),
    )
    result = check_make_help.main(["--print-index"] if mode == "index" else [])
    captured = capsys.readouterr()
    assert captured.err == ""
    if mode == "index":
        assert result == 0
        assert captured.out == f"  {'alpha':<30} available\n  {'beta':<30} available\n"
    elif mode == "complete":
        assert result == 0
        assert captured.out == "make help lists all 2 public targets\n"
    else:
        assert result == 1
        assert captured.out == "Makefile targets missing from `make help`:\n  - beta\n"


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
    assert "make/" in proc.stdout and ".mk:" in proc.stdout


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
