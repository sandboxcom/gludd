"""Task-ledger and pytest adapters for the executable backlog audit."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.validation.backlog_audit import (
    EVIDENCE_TIMEOUT_SECONDS,
    BacklogExecutionError,
    EvidenceResultPlugin,
    audit_task_ledger,
    run_evidence_tests,
)
from general_ludd.validation.backlog_sources import (
    MAX_EVIDENCE_IDS,
    MAX_LEDGER_BYTES,
    MAX_TASKS,
    MAX_TOUCHED_FILES,
    BacklogSourceError,
    confined_file_reader,
    load_task_ledger,
)


def _write_task_repo(
    root: Path,
    task_line: str,
    *,
    source: str = "src/feature.py",
    source_text: str = "VALUE = 1\n",
) -> None:
    source_path = root / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8")
    test_path = root / "tests/unit/test_feature.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("def test_feature():\n    assert True\n", encoding="utf-8")
    (root / "TASKS.md").write_text(task_line + "\n", encoding="utf-8")


def _report(
    nodeid: str,
    when: str,
    *,
    passed: bool = True,
    failed: bool = False,
    skipped: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        nodeid=nodeid,
        when=when,
        passed=passed,
        failed=failed,
        skipped=skipped,
    )


def test_checked_task_with_test_evidence_is_normalized(tmp_path: Path) -> None:
    """The adapter consumes the canonical parser's checked-task records."""
    _write_task_repo(
        tmp_path,
        "- [x] T-1 — add `src/feature.py` | evidence: "
        "`tests/unit/test_feature.py::test_feature` | status: completed",
    )

    tasks = load_task_ledger(tmp_path)

    assert tasks == [
        {
            "id": "T-1",
            "status": "completed",
            "evidence_test_ids": ["tests/unit/test_feature.py::test_feature"],
            "touched_files": ["src/feature.py"],
        }
    ]


def test_checked_task_without_explicit_status_becomes_complete(tmp_path: Path) -> None:
    _write_task_repo(
        tmp_path,
        "- [x] T-2 — add `src/feature.py` | evidence: "
        "tests/unit/test_feature.py::test_feature",
    )

    assert load_task_ledger(tmp_path)[0]["status"] == "complete"


def test_unchecked_tasks_are_not_backlog_completion_claims(tmp_path: Path) -> None:
    _write_task_repo(
        tmp_path,
        "- [ ] T-3 — add `src/feature.py` | evidence: "
        "`tests/unit/test_feature.py::test_feature` | status: in_progress",
    )

    assert load_task_ledger(tmp_path) == []


def test_no_test_evidence_is_retained_as_a_fail_closed_task(tmp_path: Path) -> None:
    _write_task_repo(
        tmp_path,
        "- [x] T-4 — add `src/feature.py` | evidence: abcdef12 | status: completed",
    )

    tasks = load_task_ledger(tmp_path)

    assert tasks[0]["evidence_test_ids"] == []


@pytest.mark.parametrize(
    "task_line",
    [
        "- [X] T-5 — uppercase GFM marker | evidence: x | status: completed",
        "* [x] T-5 — alternate GFM bullet | evidence: x | status: completed",
        "- [x] — missing primary id | evidence: x | status: completed",
        "- [x] T-5 — mismatch | evidence: x | status: in_progress",
    ],
)
def test_noncanonical_or_malformed_checked_items_fail_closed(
    tmp_path: Path,
    task_line: str,
) -> None:
    _write_task_repo(tmp_path, task_line)

    with pytest.raises(BacklogSourceError):
        load_task_ledger(tmp_path)


def test_ledger_path_must_remain_inside_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "TASKS.md"
    outside.write_text("- [x] T-6 done\n", encoding="utf-8")

    with pytest.raises(BacklogSourceError, match="outside"):
        load_task_ledger(repo, tasks_path=outside)


def test_ledger_size_and_task_count_are_bounded(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.md"
    ledger.write_text("x" * (MAX_LEDGER_BYTES + 1), encoding="utf-8")
    with pytest.raises(BacklogSourceError, match="size"):
        load_task_ledger(tmp_path)

    lines = [f"- [ ] T-{index} open" for index in range(MAX_TASKS + 1)]
    ledger.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(BacklogSourceError, match="task limit"):
        load_task_ledger(tmp_path)


def test_per_task_evidence_and_file_limits_are_bounded(tmp_path: Path) -> None:
    evidence = ", ".join(
        f"tests/unit/test_feature.py::test_case_{index}"
        for index in range(MAX_EVIDENCE_IDS + 1)
    )
    _write_task_repo(
        tmp_path,
        f"- [x] T-7 cap | evidence: {evidence} | status: completed",
    )
    with pytest.raises(BacklogSourceError, match="evidence ID limit"):
        load_task_ledger(tmp_path)

    files = " ".join(f"`src/file_{index}.py`" for index in range(MAX_TOUCHED_FILES + 1))
    (tmp_path / "TASKS.md").write_text(
        "- [x] T-8 cap "
        f"{files} | evidence: tests/unit/test_feature.py::test_feature "
        "| status: completed\n",
        encoding="utf-8",
    )
    with pytest.raises(BacklogSourceError, match="file limit"):
        load_task_ledger(tmp_path)


@pytest.mark.parametrize("unsafe", ["../outside.py", "/tmp/outside.py"])
def test_touched_file_paths_must_be_repo_relative(tmp_path: Path, unsafe: str) -> None:
    _write_task_repo(
        tmp_path,
        f"- [x] T-9 unsafe `{unsafe}` | evidence: "
        "tests/unit/test_feature.py::test_feature | status: completed",
    )

    with pytest.raises(BacklogSourceError, match="touched file"):
        load_task_ledger(tmp_path)


def test_confined_reader_refuses_symlink_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = True\n", encoding="utf-8")
    (repo / "escape.py").symlink_to(outside)

    read = confined_file_reader(repo)

    assert read(str(repo / "escape.py")) is None
    assert read(str(outside)) is None


def test_evidence_runner_deduplicates_and_invokes_pytest_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_task_repo(tmp_path, "- [ ] T-10 placeholder")
    node = "tests/unit/test_feature.py::test_feature"
    calls: list[tuple[list[str], list[object], Path]] = []

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        calls.append((args, plugins, Path.cwd()))
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        plugin.pytest_runtest_logreport(_report(node, "setup"))
        plugin.pytest_runtest_logreport(_report(node, "call"))
        plugin.pytest_runtest_logreport(_report(node, "teardown"))
        return int(pytest.ExitCode.OK)

    results = run_evidence_tests(tmp_path, [node, node], pytest_main=fake_main)

    assert results == {node: True}
    assert len(calls) == 1
    args, _plugins, cwd = calls[0]
    assert cwd == tmp_path.resolve()
    assert args.count(node) == 1
    assert "addopts=" in args
    assert f"--timeout={EVIDENCE_TIMEOUT_SECONDS}" in args
    assert Path.cwd() != tmp_path.resolve()
    output = capsys.readouterr().out
    assert "BACKLOG-EVIDENCE START" in output
    assert "BACKLOG-EVIDENCE PASS" in output
    assert "BACKLOG-EVIDENCE END" in output


def test_evidence_runner_marks_failure_and_missing_nodes_false(tmp_path: Path) -> None:
    _write_task_repo(tmp_path, "- [ ] T-11 placeholder")
    failed = "tests/unit/test_feature.py::test_feature"
    missing = "tests/unit/test_feature.py::test_missing"

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        plugin.pytest_runtest_logreport(_report(failed, "setup"))
        plugin.pytest_runtest_logreport(
            _report(failed, "call", passed=False, failed=True)
        )
        plugin.pytest_runtest_logreport(_report(failed, "teardown"))
        return int(pytest.ExitCode.TESTS_FAILED)

    assert run_evidence_tests(
        tmp_path,
        [failed, missing],
        pytest_main=fake_main,
    ) == {failed: False, missing: False}


def test_collection_error_fails_every_evidence_id_closed(tmp_path: Path) -> None:
    _write_task_repo(tmp_path, "- [ ] T-12 placeholder")
    node = "tests/unit/test_feature.py::test_feature"

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        plugin.pytest_collectreport(SimpleNamespace(failed=True, nodeid="tests/unit"))
        plugin.pytest_runtest_logreport(_report(node, "setup"))
        plugin.pytest_runtest_logreport(_report(node, "call"))
        plugin.pytest_runtest_logreport(_report(node, "teardown"))
        return int(pytest.ExitCode.TESTS_FAILED)

    assert run_evidence_tests(tmp_path, [node], pytest_main=fake_main) == {node: False}


def test_execution_error_restores_cwd_and_fails_closed(tmp_path: Path) -> None:
    _write_task_repo(tmp_path, "- [ ] T-13 placeholder")
    before = Path.cwd()

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args, plugins
        raise RuntimeError("boom")

    node = "tests/unit/test_feature.py::test_feature"
    assert run_evidence_tests(tmp_path, [node], pytest_main=fake_main) == {node: False}
    assert Path.cwd() == before


def test_evidence_runner_rejects_unsafe_or_oversized_node_sets(tmp_path: Path) -> None:
    with pytest.raises(BacklogExecutionError):
        run_evidence_tests(tmp_path, ["../test_escape.py::test_x"])
    with pytest.raises(BacklogExecutionError, match="evidence ID limit"):
        run_evidence_tests(
            tmp_path,
            [f"tests/unit/test_x.py::test_{index}" for index in range(MAX_EVIDENCE_IDS + 1)],
        )


def test_no_evidence_ids_never_runs_the_entire_test_suite(tmp_path: Path) -> None:
    called = False

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        nonlocal called
        del args, plugins
        called = True
        return int(pytest.ExitCode.OK)

    assert run_evidence_tests(tmp_path, [], pytest_main=fake_main) == {}
    assert called is False


def test_checked_task_with_evidence_is_verified_not_swallowed_api_drift(
    tmp_path: Path,
) -> None:
    node = "tests/unit/test_feature.py::test_feature"
    _write_task_repo(
        tmp_path,
        "- [x] T-14 — add `src/feature.py` | evidence: "
        f"`{node}` | status: completed",
    )
    calls = 0

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        nonlocal calls
        calls += 1
        assert node in args
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        for phase in ("setup", "call", "teardown"):
            plugin.pytest_runtest_logreport(_report(node, phase))
        return int(pytest.ExitCode.OK)

    report = audit_task_ledger(tmp_path, pytest_main=fake_main)

    assert calls == 1
    assert report.total_audited == 1
    assert report.verified_complete == 1
    assert report.false_claim == 0
    assert report.verdicts[0].verdict == "VERIFIED_COMPLETE"


def test_checked_task_without_test_evidence_is_false_claim(tmp_path: Path) -> None:
    _write_task_repo(
        tmp_path,
        "- [x] T-15 — add `src/feature.py` | evidence: deadbeef | status: completed",
    )

    report = audit_task_ledger(tmp_path, pytest_main=lambda *_args, **_kwargs: 0)

    assert report.false_claim == 1
    assert report.verdicts[0].verdict == "FALSE_CLAIM"
    assert any("no evidence tests" in reason for reason in report.verdicts[0].reasons)


def test_symlinked_touched_file_cannot_escape_during_audit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    test_file = repo / "tests/unit/test_feature.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_feature():\n    assert True\n", encoding="utf-8")
    (repo / "escape.py").symlink_to(outside)
    node = "tests/unit/test_feature.py::test_feature"
    (repo / "TASKS.md").write_text(
        f"- [x] T-16 — `escape.py` | evidence: `{node}` | status: completed\n",
        encoding="utf-8",
    )

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args
        plugin = plugins[0]
        assert isinstance(plugin, EvidenceResultPlugin)
        for phase in ("setup", "call", "teardown"):
            plugin.pytest_runtest_logreport(_report(node, phase))
        return int(pytest.ExitCode.OK)

    report = audit_task_ledger(repo, pytest_main=fake_main)

    assert report.false_claim == 1
    assert "absent/missing" in report.verdicts[0].reasons[0]


def test_plugin_requires_clean_setup_call_and_teardown() -> None:
    plugin = EvidenceResultPlugin()
    node = "tests/unit/test_x.py::test_x"
    plugin.pytest_runtest_logreport(_report(node, "setup"))
    plugin.pytest_runtest_logreport(_report(node, "call"))
    plugin.pytest_runtest_logreport(
        _report(node, "teardown", passed=False, failed=True)
    )

    assert plugin.results[node] is False


def test_plugin_treats_skip_as_not_passing_evidence() -> None:
    plugin = EvidenceResultPlugin()
    node = "tests/unit/test_x.py::test_x"
    plugin.pytest_runtest_logreport(_report(node, "setup"))
    plugin.pytest_runtest_logreport(
        _report(node, "call", passed=False, skipped=True)
    )
    plugin.pytest_runtest_logreport(_report(node, "teardown"))

    assert plugin.results[node] is False


def test_file_reader_refuses_non_regular_and_oversized_files(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    oversized = tmp_path / "large.py"
    oversized.write_text("x" * (MAX_LEDGER_BYTES + 1), encoding="utf-8")
    read = confined_file_reader(tmp_path)

    assert read(str(directory)) is None
    assert read(str(oversized)) is None


def test_repo_root_must_be_a_real_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(BacklogSourceError):
        load_task_ledger(missing)
    with pytest.raises(BacklogExecutionError):
        run_evidence_tests(missing, [])


def test_runner_uses_only_repo_relative_existing_test_files(tmp_path: Path) -> None:
    node = "tests/unit/missing.py::test_missing"
    calls = 0

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        nonlocal calls
        del args, plugins
        calls += 1
        return int(pytest.ExitCode.USAGE_ERROR)

    results = run_evidence_tests(tmp_path, [node], pytest_main=fake_main)

    assert results == {node: False}
    assert calls == 1


def test_task_source_rejects_duplicate_checked_ids(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        "- [x] T-17 first | evidence: tests/a.py::test_a | status: completed\n"
        "- [x] T-17 second | evidence: tests/b.py::test_b | status: completed\n",
        encoding="utf-8",
    )

    with pytest.raises(BacklogSourceError, match="duplicate"):
        load_task_ledger(tmp_path)


def test_task_source_detects_ledger_mutation_during_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_task_repo(
        tmp_path,
        "- [x] T-18 item | evidence: tests/unit/test_feature.py::test_feature",
    )
    import general_ludd.validation.backlog_sources as sources

    real_extract = sources.extract_tasks

    def mutating_extract(path: Path) -> Any:
        result = real_extract(path)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(sources, "extract_tasks", mutating_extract)

    with pytest.raises(BacklogSourceError, match="changed"):
        load_task_ledger(tmp_path)


def test_runner_restores_original_working_directory_when_nested(tmp_path: Path) -> None:
    original = Path.cwd()
    node = "tests/unit/test_feature.py::test_feature"
    _write_task_repo(tmp_path, "- [ ] T-19 placeholder")

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args, plugins
        assert Path(os.getcwd()) == tmp_path.resolve()
        return int(pytest.ExitCode.NO_TESTS_COLLECTED)

    run_evidence_tests(tmp_path, [node], pytest_main=fake_main)
    assert Path.cwd() == original


def test_relative_ledger_path_deduplicates_evidence_and_files(tmp_path: Path) -> None:
    nested = tmp_path / "metadata"
    nested.mkdir()
    (nested / "TASKS.md").write_text(
        "ordinary prose is not a task\n"
        "- [x] T-20 `src/feature.py` `src/feature.py` `not a path` "
        "| evidence: `tests/unit/test_x.py::test_x`, "
        "tests/unit/test_x.py::test_x | status: completed\n",
        encoding="utf-8",
    )

    tasks = load_task_ledger(tmp_path, tasks_path="metadata/TASKS.md")

    assert tasks[0]["evidence_test_ids"] == ["tests/unit/test_x.py::test_x"]
    assert tasks[0]["touched_files"] == ["src/feature.py"]


def test_ledger_must_be_a_regular_nonsymlink_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("- [ ] T-21 open\n", encoding="utf-8")
    link = tmp_path / "linked.md"
    link.symlink_to(outside)
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(BacklogSourceError, match="symlink"):
        load_task_ledger(tmp_path, tasks_path=link)
    with pytest.raises(BacklogSourceError, match="regular file"):
        load_task_ledger(tmp_path, tasks_path=directory)


def test_duplicate_evidence_fields_are_malformed(tmp_path: Path) -> None:
    _write_task_repo(
        tmp_path,
        "- [x] T-22 item | evidence: tests/a.py::test_a "
        "| evidence: tests/b.py::test_b | status: completed",
    )

    with pytest.raises(BacklogSourceError, match="duplicate 'evidence'"):
        load_task_ledger(tmp_path)


@pytest.mark.parametrize(
    "candidate",
    [
        "-option.py::test_x",
        "/tmp/test_x.py::test_x",
        "tests/../test_x.py::test_x",
        "tests/test_x.txt::test_x",
        "tests/test_x.py",
    ],
)
def test_malformed_evidence_tokens_do_not_become_tests(
    tmp_path: Path,
    candidate: str,
) -> None:
    _write_task_repo(
        tmp_path,
        f"- [x] T-23 item | evidence: `{candidate}` | status: completed",
    )

    assert load_task_ledger(tmp_path)[0]["evidence_test_ids"] == []


def test_confined_reader_accepts_relative_regular_file_and_missing_is_none(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    read = confined_file_reader(tmp_path)

    assert read("source.py") == "VALUE = 1\n"
    assert read("missing.py") is None


def test_plugin_ignores_non_test_phase_and_bounds_collection_errors() -> None:
    plugin = EvidenceResultPlugin()
    plugin.pytest_runtest_logreport(_report("tests/x.py::test_x", "collect"))
    plugin.pytest_collectreport(SimpleNamespace(failed=False, nodeid="tests"))
    plugin.collection_errors.extend(["error"] * MAX_EVIDENCE_IDS)
    plugin.pytest_collectreport(SimpleNamespace(failed=True, nodeid="overflow"))

    assert plugin.results == {}
    assert len(plugin.collection_errors) == MAX_EVIDENCE_IDS


@pytest.mark.parametrize(
    "node_id",
    [
        123,
        "",
        "tests/test_x.py",
        "tests/test_x.txt::test_x",
        "/tmp/test_x.py::test_x",
        "-option.py::test_x",
        "tests/../test_x.py::test_x",
        "tests/test_x.py::test_x\n",
    ],
)
def test_execution_rejects_each_malformed_node_shape(
    tmp_path: Path,
    node_id: object,
) -> None:
    with pytest.raises(BacklogExecutionError):
        run_evidence_tests(tmp_path, [node_id])  # type: ignore[list-item]


def test_execution_rejects_symlinked_test_escape(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    outside = tmp_path.parent / "outside-evidence.py"
    outside.write_text("def test_x(): pass\n", encoding="utf-8")
    (tests / "test_escape.py").symlink_to(outside)

    with pytest.raises(BacklogExecutionError, match="escapes"):
        run_evidence_tests(tmp_path, ["tests/test_escape.py::test_x"])


def test_unknown_pytest_exit_code_fails_closed(tmp_path: Path) -> None:
    _write_task_repo(tmp_path, "- [ ] T-24 placeholder")
    node = "tests/unit/test_feature.py::test_feature"

    def fake_main(args: list[str], *, plugins: list[object]) -> int:
        del args, plugins
        return 99

    assert run_evidence_tests(tmp_path, [node], pytest_main=fake_main) == {node: False}


def test_non_numeric_pytest_exit_code_fails_closed(tmp_path: Path) -> None:
    _write_task_repo(tmp_path, "- [ ] T-25 placeholder")
    node = "tests/unit/test_feature.py::test_feature"

    def fake_main(args: list[str], *, plugins: list[object]) -> Any:
        del args, plugins
        return None

    assert run_evidence_tests(tmp_path, [node], pytest_main=fake_main) == {node: False}


def test_audit_rejects_malformed_source_adapter_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.validation.backlog_audit as audit_module

    monkeypatch.setattr(
        audit_module,
        "load_task_ledger",
        lambda *_args, **_kwargs: [{"evidence_test_ids": ("not", "a", "list")}],
    )

    with pytest.raises(BacklogExecutionError, match="malformed evidence"):
        audit_module.audit_task_ledger(tmp_path)


def test_repo_root_file_is_not_a_directory(tmp_path: Path) -> None:
    root_file = tmp_path / "root.txt"
    root_file.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(BacklogSourceError, match="not a directory"):
        load_task_ledger(root_file)
    with pytest.raises(BacklogExecutionError, match="not a directory"):
        run_evidence_tests(root_file, [])
