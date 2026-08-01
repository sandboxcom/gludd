"""Behavioral coverage for incremental streamed pytest-failure triage."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import triage_failures


def test_parse_runtime_lines_supports_xdist_summary_and_classic_output() -> None:
    records = triage_failures.parse_runtime_lines(
        [
            "[gw1] [ 12%] FAILED tests/unit/test_alpha.py::test_one",
            "[gw0] [ 13%] ERROR tests/unit/test_beta.py::test_setup",
            "tests/unit/test_gamma.py::test_three FAILED [ 14%]",
            "FAILED tests/unit/test_delta.py::test_four - ValueError: bad value",
            "ERROR collecting tests/unit/test_import.py",
        ]
    )

    assert [(record.kind, record.nodeid, record.cause) for record in records] == [
        ("FAILED", "tests/unit/test_alpha.py::test_one", "runtime_failure"),
        ("ERROR", "tests/unit/test_beta.py::test_setup", "runtime_error"),
        ("FAILED", "tests/unit/test_gamma.py::test_three", "runtime_failure"),
        ("FAILED", "tests/unit/test_delta.py::test_four", "ValueError"),
        ("ERROR", "tests/unit/test_import.py", "collection_error"),
    ]


def test_parse_runtime_lines_deduplicates_and_upgrades_summary_cause() -> None:
    records = triage_failures.parse_runtime_lines(
        [
            "[gw1] [ 12%] FAILED tests/unit/test_alpha.py::test_one",
            "FAILED tests/unit/test_alpha.py::test_one - AssertionError: assert 0",
            "FAILED tests/unit/test_alpha.py::test_one - AssertionError: assert 0",
        ]
    )

    assert records == [
        triage_failures.FailureRecord(
            kind="FAILED",
            nodeid="tests/unit/test_alpha.py::test_one",
            cause="AssertionError",
        )
    ]


def test_parser_normalises_fallback_causes_ansi_and_ignores_noise() -> None:
    records = triage_failures.parse_runtime_lines(
        [
            "progress without a result",
            "\x1b[31mFAILED tests/unit/test_assert.py::test_it - assert False\x1b[0m",
            "FAILED tests/unit/test_failed.py::test_it - Failed: did not raise",
            "ERROR tests/unit/test_unknown.py::test_it - fixture unavailable",
        ]
    )

    assert [(record.nodeid, record.cause) for record in records] == [
        ("tests/unit/test_assert.py::test_it", "AssertionError"),
        ("tests/unit/test_failed.py::test_it", "TestFailure"),
        ("tests/unit/test_unknown.py::test_it", "summary_error"),
    ]


def test_known_failure_loader_reads_ratchet_and_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ratchet = tmp_path / "ratchet.yml"
    baseline = tmp_path / "BASELINE.md"
    ratchet.write_text("test: tests/unit/test_ratchet.py\nnot a test\n", encoding="utf-8")
    baseline.write_text("- tests/integration/test_base.py is known\n", encoding="utf-8")
    monkeypatch.setattr(triage_failures, "RATCHET_FILE", ratchet)
    monkeypatch.setattr(triage_failures, "BASELINE_FILE", baseline)

    assert triage_failures._load_known_failures() == {
        "tests/unit/test_ratchet.py",
        "tests/integration/test_base.py",
    }


def test_collect_runner_combines_output_and_converts_runner_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        triage_failures.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="stdout", stderr="stderr"),
    )
    assert triage_failures._run_pytest_collect() == "stdoutstderr"

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("collect timed out")

    monkeypatch.setattr(triage_failures.subprocess, "run", raise_timeout)
    assert triage_failures._run_pytest_collect() == "collect timed out"


def test_runtime_log_emits_only_appended_failures_in_delta(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "[gw1] [ 1%] FAILED tests/unit/test_alpha.py::test_one\n",
        encoding="utf-8",
    )

    first = triage_failures.triage_runtime_log(log, state)
    log.write_text(
        log.read_text(encoding="utf-8")
        + "[gw0] [ 2%] ERROR tests/unit/test_beta.py::test_setup\n",
        encoding="utf-8",
    )
    second = triage_failures.triage_runtime_log(log, state)
    third = triage_failures.triage_runtime_log(log, state)

    assert first["counts"] == {
        "total": 1,
        "failed": 1,
        "error": 0,
        "new": 1,
        "preexisting": 0,
        "delta": 1,
        "updated": 0,
    }
    assert [item["nodeid"] for item in second["delta"]["new"]] == [
        "tests/unit/test_beta.py::test_setup"
    ]
    assert second["counts"]["total"] == 2
    assert second["counts"]["delta"] == 1
    assert third["delta"] == {"new": [], "updated": []}
    assert third["counts"]["delta"] == 0


def test_partial_status_line_is_held_until_newline_arrives(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_alpha.py::test_one - Assert",
        encoding="utf-8",
    )

    partial = triage_failures.triage_runtime_log(log, state)
    with log.open("a", encoding="utf-8") as stream:
        stream.write("ionError: assert 0\n")
    completed = triage_failures.triage_runtime_log(log, state)

    assert partial["counts"]["total"] == 0
    assert partial["cursor"]["offset"] == 0
    assert completed["counts"]["total"] == 1
    assert completed["delta"]["new"][0]["cause"] == "AssertionError"


def test_runtime_log_groups_failures_by_file_and_root_cause(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "\n".join(
            [
                "FAILED tests/unit/test_alpha.py::test_one - AssertionError: one",
                "FAILED tests/unit/test_alpha.py::test_two - AssertionError: two",
                "ERROR tests/unit/test_beta.py::test_setup - RuntimeError: setup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = triage_failures.triage_runtime_log(log, state)

    assert payload["files"] == [
        {
            "file": "tests/unit/test_alpha.py",
            "total": 2,
            "failed": 2,
            "error": 0,
            "new": 2,
            "preexisting": 0,
        },
        {
            "file": "tests/unit/test_beta.py",
            "total": 1,
            "failed": 0,
            "error": 1,
            "new": 1,
            "preexisting": 0,
        },
    ]
    assert payload["root_causes"] == [
        {
            "cause": "AssertionError",
            "total": 2,
            "files": ["tests/unit/test_alpha.py"],
        },
        {
            "cause": "RuntimeError",
            "total": 1,
            "files": ["tests/unit/test_beta.py"],
        },
    ]


def test_later_summary_updates_cause_without_readding_nodeid(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    live = "[gw1] [ 1%] FAILED tests/unit/test_alpha.py::test_one\n"
    log.write_text(live, encoding="utf-8")
    triage_failures.triage_runtime_log(log, state)

    with log.open("a", encoding="utf-8") as stream:
        stream.write(
            "FAILED tests/unit/test_alpha.py::test_one - FileNotFoundError: gone\n"
        )
    payload = triage_failures.triage_runtime_log(log, state)

    assert payload["delta"]["new"] == []
    assert payload["delta"]["updated"] == [
        {
            "kind": "FAILED",
            "nodeid": "tests/unit/test_alpha.py::test_one",
            "file": "tests/unit/test_alpha.py",
            "cause": "FileNotFoundError",
            "classification": "new",
        }
    ]
    assert payload["counts"]["total"] == 1


def test_truncated_log_resets_cursor_and_snapshot(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_old.py::test_old - AssertionError\n",
        encoding="utf-8",
    )
    triage_failures.triage_runtime_log(log, state)

    log.write_text(
        "ERROR tests/unit/test_new.py::test_new - TypeError\n",
        encoding="utf-8",
    )
    payload = triage_failures.triage_runtime_log(log, state)

    assert payload["cursor"]["reset"] is True
    assert [item["nodeid"] for item in payload["delta"]["new"]] == [
        "tests/unit/test_new.py::test_new"
    ]
    assert payload["counts"]["total"] == 1


def test_corrupt_state_fails_safe_by_rebuilding_snapshot(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_alpha.py::test_one - LookupError\n",
        encoding="utf-8",
    )
    state.write_text("{broken", encoding="utf-8")

    payload = triage_failures.triage_runtime_log(log, state)

    assert payload["cursor"]["reset"] is True
    assert payload["counts"]["total"] == 1
    assert json.loads(state.read_text(encoding="utf-8"))["version"] == 1


def test_state_decoder_drops_malformed_records() -> None:
    assert triage_failures._state_records(None) == {}
    assert triage_failures._state_records(
        {
            1: {"kind": "FAILED", "cause": "AssertionError"},
            "tests/unit/test_bad.py::test_it": {"kind": "UNKNOWN", "cause": 7},
            "tests/unit/test_good.py::test_it": {
                "kind": "ERROR",
                "cause": "RuntimeError",
            },
        }
    ) == {
        "tests/unit/test_good.py::test_it": triage_failures.FailureRecord(
            kind="ERROR",
            nodeid="tests/unit/test_good.py::test_it",
            cause="RuntimeError",
        )
    }


def test_default_state_is_namespaced_and_empty_log_is_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "gate.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setattr(triage_failures.tempfile, "gettempdir", lambda: str(tmp_path))

    payload = triage_failures.triage_runtime_log(log)

    assert Path(payload["state"]).parent == tmp_path
    assert Path(payload["state"]).name.startswith("gludd-")
    assert payload["counts"]["total"] == 0
    assert payload["cursor"]["offset"] == 0


def test_runtime_classification_uses_existing_ratchet_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_known.py::test_one - AssertionError\n"
        "FAILED tests/unit/test_new.py::test_two - AssertionError\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        triage_failures,
        "_load_known_failures",
        lambda: {"tests/unit/test_known.py"},
    )

    payload = triage_failures.triage_runtime_log(log, state)

    assert payload["counts"]["new"] == 1
    assert payload["counts"]["preexisting"] == 1
    assert [item["classification"] for item in payload["delta"]["new"]] == [
        "preexisting",
        "new",
    ]


def test_runtime_main_prints_compact_json_and_returns_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_alpha.py::test_one - AssertionError\n",
        encoding="utf-8",
    )

    exit_code = triage_failures.main(
        ["--log", str(log), "--state", str(state), "--format", "json"]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "\n" not in output.rstrip("\n")
    assert json.loads(output)["mode"] == "runtime_log"


def test_runtime_main_missing_log_emits_structured_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.log"

    exit_code = triage_failures.main(["--log", str(missing), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {
        "mode": "runtime_log",
        "error": "log_not_found",
        "log": str(missing.resolve()),
    }


def test_no_log_retains_collect_only_human_behavior(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        triage_failures,
        "_run_pytest_collect",
        lambda: "ERROR collecting tests/unit/test_import.py\n",
    )
    monkeypatch.setattr(triage_failures, "_load_known_failures", set)

    exit_code = triage_failures.main([])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Total collection errors: 1" in output
    assert "NEW (must fix immediately): 1" in output


@pytest.mark.parametrize(
    ("collect_output", "known", "expected_fragment"),
    [
        ("collected 42 items\n", set(), "PASS: 0 collection errors"),
        (
            "ERROR collecting tests/unit/test_known.py\n",
            {"tests/unit/test_known.py"},
            "No new failures to fix. Preexisting failures (1)",
        ),
    ],
)
def test_collect_only_green_and_preexisting_paths(
    collect_output: str,
    known: set[str],
    expected_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(triage_failures, "_run_pytest_collect", lambda: collect_output)
    monkeypatch.setattr(triage_failures, "_load_known_failures", lambda: known)

    assert triage_failures.main([]) == 0
    assert expected_fragment in capsys.readouterr().out


def test_runtime_human_format_is_actionable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "gate.log"
    state = tmp_path / "triage-state.json"
    log.write_text(
        "FAILED tests/unit/test_alpha.py::test_one - AssertionError\n",
        encoding="utf-8",
    )

    exit_code = triage_failures.main(
        ["--log", str(log), "--state", str(state), "--format", "human"]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "total=1 failed=1 error=0 delta=1" in output
    assert "FAILED tests/unit/test_alpha.py::test_one (AssertionError)" in output


def test_make_target_and_contract_expose_runtime_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    body = makefile.split("triage-failures:", 1)[1].split("\n\n", 1)[0]
    contract = json.loads(
        (root / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "$(LOG)" in body
    assert "$(TRIAGE_STATE)" in body
    assert "$(TRIAGE_FORMAT)" in body
    triage_contract = next(
        item for item in contract["targets"] if item["name"] == "triage-failures"
    )
    assert triage_contract["make_variables"] == [
        "LOG",
        "TRIAGE_STATE",
        "TRIAGE_FORMAT",
    ]
