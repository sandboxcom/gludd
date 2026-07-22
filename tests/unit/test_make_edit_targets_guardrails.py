from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
REPLACE_TEXT = ROOT / "scripts" / "replace_text.py"


def _target_block(target: str) -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    pattern = rf"^{re.escape(target)}:[^\n]*\n(?P<body>(?:\t.*\n)+)"
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"Makefile target {target} not found"
    return match.group("body")


def test_patch_test_uses_fail_closed_replace_helper() -> None:
    body = _target_block("patch-test")

    assert "scripts/replace_text.py" in body
    assert "mktemp /tmp/gludd-patch-old." in body
    assert "mktemp /tmp/gludd-patch-new." in body
    assert "python3 -c" not in body
    assert ".replace(" not in body


def _run_replace_text(target: Path, old: Path, new: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPLACE_TEXT), str(target), str(old), str(new)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def test_replace_text_script_fails_closed_for_absent_match(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    target.write_text("alpha\n", encoding="utf-8")
    old.write_text("missing", encoding="utf-8")
    new.write_text("beta", encoding="utf-8")

    result = _run_replace_text(target, old, new)

    assert result.returncode == 1
    assert "old text not found" in result.stderr
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_replace_text_script_fails_closed_for_duplicate_match(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    target.write_text("alpha alpha", encoding="utf-8")
    old.write_text("alpha", encoding="utf-8")
    new.write_text("beta", encoding="utf-8")

    result = _run_replace_text(target, old, new)

    assert result.returncode == 1
    assert "old text found 2 times" in result.stderr
    assert target.read_text(encoding="utf-8") == "alpha alpha"


def test_replace_text_script_reports_success_after_single_replacement(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    target.write_text("alpha\n", encoding="utf-8")
    old.write_text("alpha", encoding="utf-8")
    new.write_text("beta", encoding="utf-8")

    result = _run_replace_text(target, old, new)

    assert result.returncode == 0
    assert "Replaced 1 occurrence" in result.stdout
    assert target.read_text(encoding="utf-8") == "beta\n"


CHECK_NO_PROMPT = ROOT / "scripts" / "check_no_prompt_prone_edit_tools.py"


def test_no_prompt_prone_checker_exists_and_backs_make_target() -> None:
    body = _target_block("check-no-prompt-prone-edit-tools")

    assert CHECK_NO_PROMPT.exists()
    assert "scripts/check_no_prompt_prone_edit_tools.py" in body


def test_no_prompt_prone_checker_fails_on_forbidden_edit_tool(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "AGENTS.md"
    policy.write_text("use apply_patch here\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECK_NO_PROMPT), str(policy)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Prompt-prone edit tooling references found" in result.stdout
    assert "apply_patch" in result.stdout


def test_no_prompt_prone_checker_accepts_make_edit_targets(tmp_path: Path) -> None:
    policy = tmp_path / "AGENTS.md"
    policy.write_text(
        "Use make write-text, make append-text, make replace-text, and make copy-file.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(CHECK_NO_PROMPT), str(policy)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "No prompt-prone edit tooling references found" in result.stdout


def test_base64_edit_targets_use_make_variables_not_shell_text_env() -> None:
    write_body = _target_block("write-text-b64")
    replace_body = _target_block("replace-text-b64")

    assert "TEXT_B64" in write_body
    assert "FILE_PATH" in write_body
    assert "printf" not in write_body
    assert "OLD_B64" in replace_body
    assert "NEW_B64" in replace_body
    assert "mktemp /tmp/gludd-old." in replace_body
    assert "mktemp /tmp/gludd-new." in replace_body
    assert "scripts/replace_text.py" in replace_body


REPLACE_LINES = ROOT / "scripts" / "replace_lines.py"


def test_replace_lines_accepts_tmp_gludd_path_after_private_tmp_resolution() -> None:
    target = Path(f"/tmp/gludd-replace-lines-{os.getpid()}.txt")
    new_file = Path(f"/tmp/gludd-replace-lines-new-{os.getpid()}.txt")
    try:
        target.write_text(
            "one" + chr(10) + "two" + chr(10) + "three" + chr(10),
            encoding="utf-8",
        )
        new_file.write_text(chr(9) + "TWO" + chr(10), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(REPLACE_LINES),
                str(target),
                "2",
                "2",
                str(new_file),
            ],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

        expected = "one" + chr(10) + chr(9) + "TWO" + chr(10) + "three" + chr(10)
        assert result.returncode == 0, result.stderr
        assert target.read_text(encoding="utf-8") == expected
    finally:
        target.unlink(missing_ok=True)
        new_file.unlink(missing_ok=True)


def test_git_diff_honors_explicit_files_scope() -> None:
    body = _target_block("git-diff")

    assert "" in body


def test_ship_commit_emits_observable_phase_markers() -> None:
    body = _target_block("ship-commit")

    assert "Running pre-commit collection check" in body
    assert "Committing staged changes" in body


def test_no_prompt_prone_checker_ignores_missing_optional_paths(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.md"

    result = subprocess.run(
        [sys.executable, str(CHECK_NO_PROMPT), str(missing)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "missing configured scan path" not in result.stdout


def test_no_prompt_prone_checker_is_standard_gate_dependency() -> None:
    gate_line = next(line for line in MAKEFILE.read_text().splitlines() if line.startswith("gate:"))
    gate_lite_line = next(line for line in MAKEFILE.read_text().splitlines() if line.startswith("gate-lite:"))

    assert "check-no-prompt-prone-edit-tools" in gate_line
    assert "check-no-prompt-prone-edit-tools" in gate_lite_line
