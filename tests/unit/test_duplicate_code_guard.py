"""Contracts for staged and committed mature duplicate-code admission."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import check_duplicate_code, staged_snapshot

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "duplicate_code.json"


def _policy_payload() -> dict[str, Any]:
    return {
        "path": ["src", "scripts"],
        "minLines": 12,
        "minTokens": 80,
        "mode": "weak",
        "ignoreIdentifiers": True,
        "ignoreLiterals": True,
        "maxSize": "500kb",
        "reporters": ["json", "silent"],
        "ignore": ["**/fixtures/**"],
        "noTips": True,
    }


def _config(path: Path) -> Path:
    path.write_text(json.dumps(_policy_payload()), encoding="utf-8")
    return path


def test_policy_is_bounded_and_uses_renamed_clone_detection() -> None:
    policy = check_duplicate_code.load_policy(CONFIG.read_bytes())

    assert policy.production_paths
    assert policy.min_lines >= 12
    assert policy.min_tokens >= 80
    assert policy.max_size == "500kb"
    assert policy.workers <= 2
    assert policy.ignore_identifiers is True
    assert policy.ignore_literals is True


@pytest.mark.parametrize(
    "case",
    [
        "not-object",
        "keys",
        "paths-type",
        "unsafe-path",
        "duplicate-path",
        "min-lines",
        "min-tokens",
        "mode",
        "max-size",
        "reporters",
        "identifiers",
        "literals",
        "tips",
        "ignore",
    ],
)
def test_policy_drift_fails_closed(case: str) -> None:
    payload: Any = _policy_payload()
    if case == "not-object":
        payload = []
    elif case == "keys":
        payload["unexpected"] = True
    elif case == "paths-type":
        payload["path"] = []
    elif case == "unsafe-path":
        payload["path"] = ["../src"]
    elif case == "duplicate-path":
        payload["path"] = ["src", "src"]
    elif case == "min-lines":
        payload["minLines"] = 11
    elif case == "min-tokens":
        payload["minTokens"] = True
    elif case == "mode":
        payload["mode"] = "strict"
    elif case == "max-size":
        payload["maxSize"] = "1mb"
    elif case == "reporters":
        payload["reporters"] = ["console"]
    elif case == "identifiers":
        payload["ignoreIdentifiers"] = False
    elif case == "literals":
        payload["ignoreLiterals"] = False
    elif case == "tips":
        payload["noTips"] = False
    elif case == "ignore":
        payload["ignore"] = [""]

    with pytest.raises(check_duplicate_code.DuplicateCodeError):
        check_duplicate_code.load_policy(json.dumps(payload).encode())


@pytest.mark.parametrize("data", [b"{", b"\xff"])
def test_policy_requires_utf8_json(data: bytes) -> None:
    with pytest.raises(check_duplicate_code.DuplicateCodeError):
        check_duplicate_code.load_policy(data)


def test_config_path_must_be_inside_repository(tmp_path: Path) -> None:
    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="outside"):
        check_duplicate_code._repository_relative(
            tmp_path / "repository",
            tmp_path / "policy.json",
        )


def test_staged_guard_builds_head_baseline_then_scans_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    engine = tmp_path / "jscpd"
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    events: list[tuple[str, tuple[str, ...]]] = []
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(
        check_duplicate_code,
        "discover_staged_paths",
        lambda _root: ("src/new.py",),
    )
    monkeypatch.setattr(
        check_duplicate_code,
        "read_index_blob",
        lambda _root, _path: staged_snapshot.IndexBlob("100644", config.read_bytes()),
    )

    def fake_ref(_root: Path, ref: str, _dest: Path, paths: tuple[str, ...]) -> None:
        events.append((f"ref:{ref}", paths))

    def fake_index(_root: Path, _dest: Path, paths: tuple[str, ...]) -> None:
        events.append(("index", paths))

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append((command, cwd))
        baseline = Path(command[command.index("--baseline") + 1])
        if "--update-baseline" in command:
            baseline.write_text('{"version":1}', encoding="utf-8")
        else:
            output = Path(command[command.index("--output") + 1])
            output.mkdir(parents=True)
            (output / "jscpd-report.json").write_text(
                json.dumps(
                    {
                        "duplicates": [],
                        "statistics": {
                            "total": {
                                "clones": 0,
                                "newClones": 0,
                                "duplicatedLines": 0,
                                "newDuplicatedLines": 0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(check_duplicate_code, "materialize_ref", fake_ref)
    monkeypatch.setattr(check_duplicate_code, "materialize_index", fake_index)
    monkeypatch.setattr(check_duplicate_code.subprocess, "run", fake_run)

    result = check_duplicate_code.run_guard(
        root=tmp_path,
        config_path=config,
        engine=engine,
        source="staged",
        base_ref="HEAD",
        current_ref="HEAD",
    )

    assert result == 0
    assert events == [
        ("ref:HEAD", ("scripts", "src")),
        ("index", ("scripts", "src")),
    ]
    assert len(calls) == 2
    assert "--update-baseline" in calls[0][0]
    assert "--fail-on-new-clones" in calls[1][0]
    assert calls[0][0][-4:] == ["--workers", "2", "--reporters", "silent"]
    assert "json,silent" in calls[1][0]


def test_committed_guard_compares_feature_head_to_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    engine = tmp_path / "jscpd"
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    refs: list[str] = []

    monkeypatch.setattr(
        check_duplicate_code,
        "discover_changed_paths",
        lambda _root, base, current: (
            refs.extend([base, current]) or ("src/changed.py",)
        ),
    )
    monkeypatch.setattr(
        check_duplicate_code,
        "read_ref_blob",
        lambda _root, _ref, _path: config.read_bytes(),
    )

    def fake_materialize(
        _root: Path, ref: str, _dest: Path, _paths: tuple[str, ...]
    ) -> None:
        refs.append(ref)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        baseline = Path(command[command.index("--baseline") + 1])
        if "--update-baseline" in command:
            baseline.write_text('{"version":1}', encoding="utf-8")
        else:
            output = Path(command[command.index("--output") + 1])
            output.mkdir(parents=True)
            (output / "jscpd-report.json").write_text(
                '{"duplicates":[],"statistics":{"total":{"clones":0,'
                '"newClones":0,"duplicatedLines":0,"newDuplicatedLines":0}}}',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(check_duplicate_code, "materialize_ref", fake_materialize)
    monkeypatch.setattr(check_duplicate_code.subprocess, "run", fake_run)

    assert (
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=engine,
            source="committed",
            base_ref="development",
            current_ref="HEAD",
        )
        == 0
    )
    assert refs == ["development", "HEAD", "development", "HEAD"]


def test_json_report_keeps_existing_totals_and_lists_every_new_clone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    report = tmp_path / "jscpd-report.json"
    report.write_text(
        json.dumps(
            {
                "duplicates": [
                    {
                        "format": "python",
                        "kind": "exact",
                        "lines": 12,
                        "tokens": 90,
                        "isNew": False,
                        "firstFile": {
                            "name": str(candidate / "src" / "old_a.py"),
                            "start": 1,
                            "end": 12,
                        },
                        "secondFile": {
                            "name": str(candidate / "src" / "old_b.py"),
                            "start": 2,
                            "end": 13,
                        },
                    },
                    {
                        "format": "python",
                        "kind": "renamed",
                        "lines": 14,
                        "tokens": 101,
                        "isNew": True,
                        "firstFile": {
                            "name": str(candidate / "src" / "new_a.py"),
                            "start": 7,
                            "end": 20,
                        },
                        "secondFile": {
                            "name": str(candidate / "scripts" / "new_b.py"),
                            "start": 30,
                            "end": 43,
                        },
                    },
                    {
                        "format": "typescript",
                        "kind": "exact",
                        "lines": 13,
                        "tokens": 88,
                        "isNew": True,
                        "firstFile": {
                            "name": str(candidate / ".opencode" / "plugin" / "a.ts"),
                            "start": 10,
                            "end": 22,
                        },
                        "secondFile": {
                            "name": str(candidate / ".opencode" / "plugin" / "b.ts"),
                            "start": 40,
                            "end": 52,
                        },
                    },
                ],
                "statistics": {
                    "total": {
                        "clones": 3,
                        "newClones": 2,
                        "duplicatedLines": 39,
                        "newDuplicatedLines": 27,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    check_duplicate_code.emit_report(report, candidate)

    output = capsys.readouterr().out
    assert "clones=3 existing=1 new=2" in output
    assert "duplicated_lines=39 new_duplicated_lines=27" in output
    assert "src/new_a.py:7-20" in output
    assert "scripts/new_b.py:30-43" in output
    assert ".opencode/plugin/a.ts:10-22" in output
    assert ".opencode/plugin/b.ts:40-52" in output
    assert "src/old_a.py" not in output


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"duplicates": [], "statistics": {"total": {"clones": 1}}},
        {
            "duplicates": [{"isNew": True}],
            "statistics": {
                "total": {
                    "clones": 1,
                    "newClones": 1,
                    "duplicatedLines": 12,
                    "newDuplicatedLines": 12,
                }
            },
        },
    ],
)
def test_malformed_json_report_fails_closed(
    tmp_path: Path,
    payload: object,
) -> None:
    report = tmp_path / "jscpd-report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(check_duplicate_code.DuplicateCodeError):
        check_duplicate_code.emit_report(report, tmp_path)


def _clone(candidate: Path, *, is_new: bool, relative: bool = False) -> dict[str, Any]:
    first = Path("src/a.py") if relative else candidate / "src" / "a.py"
    second = Path("scripts/b.py") if relative else candidate / "scripts" / "b.py"
    return {
        "format": "python",
        "kind": "exact",
        "lines": 12,
        "tokens": 80,
        "isNew": is_new,
        "firstFile": {"name": str(first), "start": 1, "end": 12},
        "secondFile": {"name": str(second), "start": 2, "end": 13},
    }


def _report_payload(candidate: Path, duplicates: list[dict[str, Any]]) -> dict[str, Any]:
    new_count = sum(duplicate["isNew"] is True for duplicate in duplicates)
    return {
        "duplicates": duplicates,
        "statistics": {
            "total": {
                "clones": len(duplicates),
                "newClones": new_count,
                "duplicatedLines": len(duplicates) * 12,
                "newDuplicatedLines": new_count * 12,
            }
        },
    }


@pytest.mark.parametrize(
    "case",
    [
        "root",
        "duplicates",
        "statistics",
        "count",
        "lines",
        "flag",
        "new-count",
        "format",
        "kind",
        "number",
        "location",
        "range",
        "outside",
    ],
)
def test_json_report_schema_and_locations_fail_closed(
    tmp_path: Path,
    case: str,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    payload: Any = _report_payload(candidate, [_clone(candidate, is_new=True)])
    if case == "root":
        payload = []
    elif case == "duplicates":
        payload["duplicates"] = {}
    elif case == "statistics":
        payload["statistics"] = []
    elif case == "count":
        payload["statistics"]["total"]["clones"] = 2
    elif case == "lines":
        payload["statistics"]["total"]["newDuplicatedLines"] = 13
    elif case == "flag":
        payload["duplicates"][0]["isNew"] = "yes"
    elif case == "new-count":
        payload["statistics"]["total"]["newClones"] = 0
    elif case == "format":
        payload["duplicates"][0]["format"] = ""
    elif case == "kind":
        payload["duplicates"][0]["kind"] = None
    elif case == "number":
        payload["duplicates"][0]["tokens"] = True
    elif case == "location":
        payload["duplicates"][0]["firstFile"] = []
    elif case == "range":
        payload["duplicates"][0]["firstFile"]["start"] = 0
    elif case == "outside":
        payload["duplicates"][0]["firstFile"]["name"] = str(tmp_path / "outside.py")
    report = tmp_path / "jscpd-report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(check_duplicate_code.DuplicateCodeError):
        check_duplicate_code.emit_report(report, candidate)


def test_relative_report_locations_and_detail_safety_bound(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    duplicates = [_clone(tmp_path, is_new=True, relative=True) for _ in range(101)]
    report = tmp_path / "jscpd-report.json"
    report.write_text(
        json.dumps(_report_payload(tmp_path, duplicates)),
        encoding="utf-8",
    )

    assert check_duplicate_code.emit_report(report, tmp_path) == 101

    output = capsys.readouterr().out
    assert "first=src/a.py:1-12" in output
    assert "details_truncated=1 safety_limit=100" in output


def test_empty_and_missing_reports_fail_closed(tmp_path: Path) -> None:
    report = tmp_path / "jscpd-report.json"
    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="unavailable"):
        check_duplicate_code.emit_report(report, tmp_path)
    report.write_bytes(b"")
    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="size"):
        check_duplicate_code.emit_report(report, tmp_path)


def test_control_and_nested_production_paths_always_trigger_scan() -> None:
    production = ("scripts", "src")

    assert check_duplicate_code._needs_scan(
        ("config/duplicate_code.json",), production, "config/duplicate_code.json"
    )
    assert check_duplicate_code._needs_scan(("src/nested/app.py",), production, "policy")
    assert not check_duplicate_code._needs_scan(("docs/guide.md",), production, "policy")


def test_non_production_change_skips_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    monkeypatch.setattr(
        check_duplicate_code,
        "discover_staged_paths",
        lambda _root: ("docs/guide.md",),
    )
    monkeypatch.setattr(
        check_duplicate_code,
        "read_index_blob",
        lambda _root, _path: staged_snapshot.IndexBlob("100644", config.read_bytes()),
    )

    assert (
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=tmp_path / "missing-jscpd",
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )
        == 0
    )
    assert "SKIP changed_production=0" in capsys.readouterr().out


@pytest.mark.parametrize("returncode", [1, 2, 127])
def test_engine_failures_are_never_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    engine = tmp_path / "jscpd"
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    monkeypatch.setattr(
        check_duplicate_code,
        "discover_staged_paths",
        lambda _root: ("src/new.py",),
    )
    monkeypatch.setattr(
        check_duplicate_code,
        "read_index_blob",
        lambda _root, _path: staged_snapshot.IndexBlob("100644", config.read_bytes()),
    )
    monkeypatch.setattr(check_duplicate_code, "materialize_ref", lambda *_: None)
    monkeypatch.setattr(check_duplicate_code, "materialize_index", lambda *_: None)
    monkeypatch.setattr(
        check_duplicate_code.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, returncode, "", "boom"),
    )

    assert (
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=engine,
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )
        == returncode
    )


def _staged_guard_inputs(
    monkeypatch: pytest.MonkeyPatch,
    config: Path,
) -> None:
    monkeypatch.setattr(
        check_duplicate_code,
        "discover_staged_paths",
        lambda _root: ("src/new.py",),
    )
    monkeypatch.setattr(
        check_duplicate_code,
        "read_index_blob",
        lambda _root, _path: staged_snapshot.IndexBlob("100644", config.read_bytes()),
    )
    monkeypatch.setattr(check_duplicate_code, "materialize_ref", lambda *_: None)
    monkeypatch.setattr(check_duplicate_code, "materialize_index", lambda *_: None)


def test_missing_locked_engine_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    _staged_guard_inputs(monkeypatch, config)

    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="unavailable"):
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=Path("missing-jscpd"),
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )


def test_snapshot_failures_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    monkeypatch.setattr(
        check_duplicate_code,
        "read_index_blob",
        lambda *_: (_ for _ in ()).throw(staged_snapshot.SnapshotError("broken index")),
    )

    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="broken index"):
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=Path("missing-jscpd"),
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )


def test_missing_baseline_and_engine_start_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    engine = tmp_path / "jscpd"
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    _staged_guard_inputs(monkeypatch, config)
    monkeypatch.setattr(
        check_duplicate_code.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, "", ""),
    )

    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="baseline"):
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=engine,
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )

    monkeypatch.setattr(
        check_duplicate_code.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("exec denied")),
    )
    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="cannot execute"):
        check_duplicate_code._run_engine(
            engine,
            cwd=tmp_path,
            baseline=tmp_path / "baseline.json",
            update=True,
        )


def test_success_exit_with_new_clone_report_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    engine = tmp_path / "jscpd"
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    _staged_guard_inputs(monkeypatch, config)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        baseline = Path(command[command.index("--baseline") + 1])
        if "--update-baseline" in command:
            baseline.write_text('{"version":1}', encoding="utf-8")
        else:
            output = Path(command[command.index("--output") + 1])
            output.mkdir(parents=True)
            candidate = Path(kwargs["cwd"])
            (output / "jscpd-report.json").write_text(
                json.dumps(_report_payload(candidate, [_clone(candidate, is_new=True)])),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(check_duplicate_code.subprocess, "run", fake_run)

    with pytest.raises(check_duplicate_code.DuplicateCodeError, match="returned success"):
        check_duplicate_code.run_guard(
            root=tmp_path,
            config_path=config,
            engine=engine,
            source="staged",
            base_ref="HEAD",
            current_ref="HEAD",
        )


def test_main_validate_execute_and_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path / "duplicate_code.json")
    common = [
        "--root",
        str(tmp_path),
        "--config",
        str(config),
        "--engine",
        "jscpd",
        "--source",
        "staged",
        "--base-ref",
        "HEAD",
        "--current-ref",
        "HEAD",
    ]
    assert check_duplicate_code.main([*common, "--validate-only"]) == 0
    assert "VALIDATED" in capsys.readouterr().out

    monkeypatch.setattr(check_duplicate_code, "run_guard", lambda **_: 7)
    assert check_duplicate_code.main(common) == 7

    config.write_text("{}", encoding="utf-8")
    assert check_duplicate_code.main([*common, "--validate-only"]) == 2
    assert "ERROR" in capsys.readouterr().err
