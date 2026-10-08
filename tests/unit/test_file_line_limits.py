"""Tests for the fail-closed tracked-file line-limit checker."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_file_line_limits.py"


def _load_checker() -> ModuleType:
    assert SCRIPT.is_file(), "tracked-file line-limit checker must exist"
    spec = importlib.util.spec_from_file_location("check_file_line_limits", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _policy_file(tmp_path: Path, non_text: list[dict[str, str]] | None = None) -> Path:
    path = tmp_path / "file_line_limits.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "max_lines_exclusive": 2500,
                "non_text_paths": non_text or [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_limit_is_strictly_less_than_2500_lines(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(_policy_file(tmp_path))
    (tmp_path / "allowed.txt").write_text("x\n" * 2499, encoding="utf-8")
    (tmp_path / "rejected.txt").write_text("x\n" * 2500, encoding="utf-8")

    findings = checker.audit_paths(
        tmp_path,
        ("allowed.txt", "rejected.txt"),
        policy,
    )

    assert [(item.path, item.line_count) for item in findings] == [
        ("rejected.txt", 2500)
    ]


def test_non_terminated_last_line_is_counted(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(_policy_file(tmp_path))
    (tmp_path / "rejected.txt").write_text(
        ("x\n" * 2499) + "last",
        encoding="utf-8",
    )

    findings = checker.audit_paths(tmp_path, ("rejected.txt",), policy)

    assert findings[0].line_count == 2500


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 2, "max_lines_exclusive": 2500, "non_text_paths": []},
        {"version": 1, "max_lines_exclusive": 2499, "non_text_paths": []},
        {
            "version": 1,
            "max_lines_exclusive": 2500,
            "non_text_paths": [],
            "silent_exclusions": ["generated/**"],
        },
    ],
)
def test_policy_schema_and_limit_drift_fail_closed(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    checker = _load_checker()
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(checker.PolicyError):
        checker.load_policy(path)


@pytest.mark.parametrize("path", ["", "/absolute.bin", "../escape.bin", "a/../b.bin"])
def test_non_text_policy_requires_safe_exact_paths(tmp_path: Path, path: str) -> None:
    checker = _load_checker()
    config = _policy_file(tmp_path, [{"path": path, "reason": "binary fixture"}])

    with pytest.raises(checker.PolicyError):
        checker.load_policy(config)


def test_non_text_policy_requires_unique_paths_and_reasons(tmp_path: Path) -> None:
    checker = _load_checker()
    duplicate = _policy_file(
        tmp_path,
        [
            {"path": "asset.bin", "reason": "binary fixture"},
            {"path": "asset.bin", "reason": "duplicate"},
        ],
    )
    with pytest.raises(checker.PolicyError):
        checker.load_policy(duplicate)

    missing_reason = _policy_file(tmp_path, [{"path": "asset.bin", "reason": ""}])
    with pytest.raises(checker.PolicyError):
        checker.load_policy(missing_reason)


def test_inventory_uses_shared_snapshot_boundary() -> None:
    checker = _load_checker()

    assert checker.discover_tracked_paths.__module__ == "staged_snapshot"


def test_unlisted_binary_and_unreadable_files_fail_closed(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(_policy_file(tmp_path))
    (tmp_path / "binary.dat").write_bytes(b"header\x00payload")

    with pytest.raises(checker.AuditError, match="explicit non_text_paths"):
        checker.audit_paths(tmp_path, ("binary.dat",), policy)
    with pytest.raises(checker.AuditError, match="missing or unreadable"):
        checker.audit_paths(tmp_path, ("missing.txt",), policy)


def test_explicit_binary_policy_is_exact_and_drift_checked(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(
        _policy_file(
            tmp_path,
            [{"path": "asset.bin", "reason": "PNG test fixture"}],
        )
    )
    (tmp_path / "asset.bin").write_bytes(b"png\x00payload")

    assert checker.audit_paths(tmp_path, ("asset.bin",), policy) == []

    (tmp_path / "asset.bin").write_text("now text\n", encoding="utf-8")
    with pytest.raises(checker.AuditError, match="now UTF-8 text"):
        checker.audit_paths(tmp_path, ("asset.bin",), policy)

    with pytest.raises(checker.AuditError, match="not tracked"):
        checker.audit_paths(tmp_path, (), policy)


def test_explicit_gitlink_directory_is_not_treated_as_a_file(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(
        _policy_file(
            tmp_path,
            [{"path": "external/component", "reason": "Git submodule gitlink"}],
        )
    )
    (tmp_path / "external" / "component").mkdir(parents=True)

    assert checker.audit_paths(tmp_path, ("external/component",), policy) == []


def test_symlink_blob_is_counted_without_following_its_target(tmp_path: Path) -> None:
    checker = _load_checker()
    policy = checker.load_policy(_policy_file(tmp_path))
    (tmp_path / "real.txt").write_text("text\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to("real.txt")

    assert checker.audit_paths(tmp_path, ("link.txt",), policy) == []


def test_cli_exit_codes_distinguish_findings_from_audit_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker()
    config = _policy_file(tmp_path)
    (tmp_path / "large.txt").write_text("x\n" * 2500, encoding="utf-8")

    assert checker.main(
        ["--root", str(tmp_path), "--config", str(config), "--path", "large.txt"]
    ) == 1
    assert "large.txt: 2500 lines" in capsys.readouterr().out

    assert checker.main(
        ["--root", str(tmp_path), "--config", str(config), "--path", "missing.txt"]
    ) == 2
    assert "missing or unreadable" in capsys.readouterr().err


def _git(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )


def _tracked_repository(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "line-limit@example.invalid")
    _git(tmp_path, "config", "user.name", "Line Limit Test")
    (tmp_path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "seed")


@pytest.mark.parametrize(
    ("staged_lines", "worktree_lines", "expected"),
    [(2500, 1, 1), (1, 2500, 0)],
)
def test_staged_mode_audits_index_not_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    staged_lines: int,
    worktree_lines: int,
    expected: int,
) -> None:
    checker = _load_checker()
    _tracked_repository(tmp_path)
    config = _policy_file(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("staged\n" * staged_lines, encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    tracked.write_text("worktree\n" * worktree_lines, encoding="utf-8")

    result = checker.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(config),
            "--staged",
            "--path",
            "tracked.txt",
        ]
    )

    assert result == expected
    output = capsys.readouterr()
    if expected:
        assert "tracked.txt: 2500 lines" in output.out
    else:
        assert "source=index" in output.out
