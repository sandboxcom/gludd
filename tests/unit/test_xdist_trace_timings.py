"""Timing diagnostics retain native trace identity and failure evidence."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from summarize_xdist_trace import main, summarize_log
else:
    from scripts.summarize_xdist_trace import main, summarize_log

ROOT = Path(__file__).resolve().parents[2]


def _event(kind: str, node: str = "tests/a.py::test_a", /, **values: object) -> dict[str, object]:
    return {
        "event": kind,
        "run_id": "run-a",
        "worker": "gw0",
        "pid": 101,
        "nodeid": node,
        "timestamp": 10.0 if kind == "START" else 12.0,
        **values,
    }


def _summary(
    tmp_path: Path, events: Sequence[Mapping[str, object]], *, run_id: str | None = None
) -> dict[str, object]:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "".join(json.dumps(event) + chr(10) for event in events), encoding="utf-8"
    )
    return summarize_log(path, run_id=run_id)


def _timings(summary: Mapping[str, object]) -> dict[str, object]:
    return cast(dict[str, object], summary["timings"])


def _reasons(report: Mapping[str, object]) -> list[str]:
    return [
        str(issue["reason"])
        for issue in cast(list[dict[str, object]], report["issues"])
    ]


def test_timing_pairs_rank_test_protocols_without_counting_reports_twice(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path,
        [
            _event("START"),
            _event("REPORT", outcome="failed", when="call", duration=999),
            _event("FINISH"),
            _event("START", "tests/a.py::test_b", timestamp=13),
            _event("FINISH", "tests/a.py::test_b", timestamp=16),
            _event("START", "tests/b.py::test_c", worker="gw1", pid=102, timestamp=11),
            _event("FINISH", "tests/b.py::test_c", worker="gw1", pid=102, timestamp=15),
        ],
    )
    report = _timings(summary)

    assert report["authority"] == "diagnostic-only"
    assert report["gate_evidence"] is False
    assert report["release_ready"] is False
    assert report["clock"] == "wall"
    assert report["scope"] == "test-protocol"
    assert report["aggregation"] == "sum-of-paired-test-protocol-wall-seconds"
    assert report["paired_count"] == 3
    assert report["issue_count"] == 0
    assert report["issues"] == []
    assert report["modules"] == [
        {"run_id": "run-a", "worker": "gw0", "pid": 101, "module": "tests/a.py",
         "paired_count": 2, "test_protocol_seconds": 5.0},
        {"run_id": "run-a", "worker": "gw1", "pid": 102, "module": "tests/b.py",
         "paired_count": 1, "test_protocol_seconds": 4.0},
    ]
    assert report["workers"] == [
        {"run_id": "run-a", "worker": "gw0", "pid": 101, "paired_count": 2, "test_protocol_seconds": 5.0},
        {"run_id": "run-a", "worker": "gw1", "pid": 102, "paired_count": 1, "test_protocol_seconds": 4.0},
    ]
    rows = cast(list[dict[str, object]], report["slowest_tests"])
    assert [row["nodeid"] for row in rows] == [
        "tests/b.py::test_c", "tests/a.py::test_b", "tests/a.py::test_a"
    ]
    assert [row["test_protocol_seconds"] for row in rows] == [4.0, 3.0, 2.0]
    assert rows[0]["run_id"] == "run-a"
    assert rows[0]["worker"] == "gw1"
    assert rows[0]["pid"] == 102
    assert summary["failure_nodeids"] == ["tests/a.py::test_a"]


def test_repeated_protocols_keep_module_totals_separate_per_process(tmp_path: Path) -> None:
    node = "tests/a.py::test_a[parameter]@shared"
    report = _timings(
        _summary(
            tmp_path,
            [
                _event("START", node, timestamp=1),
                _event("FINISH", node, timestamp=2),
                _event("START", node, timestamp=2),
                _event("FINISH", node, timestamp=4),
                _event("START", node, pid=202, timestamp=1),
                _event("FINISH", node, pid=202, timestamp=5),
                _event("START", node, run_id="run-b", timestamp=1),
                _event("FINISH", node, run_id="run-b", timestamp=1),
            ],
        )
    )

    assert report["paired_count"] == 4
    assert report["issue_count"] == 0
    rows = cast(list[dict[str, object]], report["modules"])
    assert rows == [
        {"run_id": "run-a", "worker": "gw0", "pid": 202, "module": "tests/a.py",
         "paired_count": 1, "test_protocol_seconds": 4.0},
        {"run_id": "run-a", "worker": "gw0", "pid": 101, "module": "tests/a.py",
         "paired_count": 2, "test_protocol_seconds": 3.0},
        {"run_id": "run-b", "worker": "gw0", "pid": 101, "module": "tests/a.py",
         "paired_count": 1, "test_protocol_seconds": 0.0},
    ]
    assert all(row["nodeid"] == node for row in cast(list[dict[str, object]], report["slowest_tests"]))


@pytest.mark.parametrize(
    "changed_identity",
    [{"run_id": "run-b"}, {"worker": "gw1"}, {"pid": 202}],
)
def test_timing_never_pairs_across_run_worker_or_process(
    tmp_path: Path, changed_identity: dict[str, object]
) -> None:
    summary = _summary(
        tmp_path,
        [
            _event("START"),
            _event("FINISH", **changed_identity),
            _event("FINISH"),
        ],
    )
    report = _timings(summary)

    assert report["paired_count"] == 1
    assert _reasons(report) == ["unmatched_finish"]


@pytest.mark.parametrize(
    "invalid_identity",
    [
        {"run_id": None}, {"run_id": ""}, {"worker": None}, {"worker": ""},
        {"pid": None}, {"pid": True}, {"pid": "101"}, {"pid": 0}, {"pid": -1},
        {"nodeid": None}, {"nodeid": ""},
    ],
)
def test_timing_refuses_missing_or_ambiguous_identity(
    tmp_path: Path, invalid_identity: dict[str, object]
) -> None:
    report = _timings(_summary(tmp_path, [_event("START", **invalid_identity)]))

    assert report["paired_count"] == 0
    assert report["issue_count"] == 1
    assert _reasons(report) == ["invalid_identity"]


@pytest.mark.parametrize("event_kind", ["START", "FINISH"])
@pytest.mark.parametrize(
    "timestamp_value",
    [None, True, "10.0", -1, float("nan"), float("inf"), float("-inf"), 10**400],
)
def test_timing_refuses_invalid_clocks_without_fabricating_duration(
    tmp_path: Path, event_kind: str, timestamp_value: object
) -> None:
    events = [_event("START"), _event("FINISH")]
    events[0 if event_kind == "START" else 1]["timestamp"] = timestamp_value
    report = _timings(_summary(tmp_path, events))

    assert report["paired_count"] == 0
    assert "invalid_timestamp" in _reasons(report)


def test_timing_refuses_negative_duration_and_clock_overlap(tmp_path: Path) -> None:
    report = _timings(
        _summary(
            tmp_path,
            [
                _event("START", timestamp=10),
                _event("FINISH", timestamp=9),
                _event("START", "tests/b.py::test_b", timestamp=20),
                _event("FINISH", "tests/b.py::test_b", timestamp=22),
                _event("START", "tests/c.py::test_c", timestamp=21),
                _event("FINISH", "tests/c.py::test_c", timestamp=23),
            ],
        )
    )

    assert report["paired_count"] == 1
    assert _reasons(report) == ["negative_duration", "overlapping_interval"]
    rows = cast(list[dict[str, object]], report["slowest_tests"])
    assert rows[0]["nodeid"] == "tests/b.py::test_b"


@pytest.mark.parametrize("second_node", ["tests/a.py::test_a", "tests/b.py::test_b"])
def test_timing_discards_overlapping_starts_then_accepts_a_new_serial_span(
    tmp_path: Path, second_node: str
) -> None:
    report = _timings(
        _summary(
            tmp_path,
            [
                _event("START"),
                _event("START", second_node, timestamp=11),
                _event("FINISH", timestamp=12),
                _event("FINISH", second_node, timestamp=13),
                _event("START", "tests/c.py::test_c", timestamp=14),
                _event("FINISH", "tests/c.py::test_c", timestamp=15),
            ],
        )
    )

    assert report["paired_count"] == 1
    assert "overlapping_start" in _reasons(report)
    assert report["modules"] == [
        {"run_id": "run-a", "worker": "gw0", "pid": 101, "module": "tests/c.py",
         "paired_count": 1, "test_protocol_seconds": 1.0}
    ]


def test_timing_keeps_unmatched_and_unfinished_evidence(tmp_path: Path) -> None:
    summary = _summary(tmp_path, [_event("FINISH"), _event("START")])
    report = _timings(summary)

    assert report["paired_count"] == 0
    assert _reasons(report) == ["unmatched_finish", "unfinished_start"]
    assert summary["unfinished"] == [{"worker": "gw0", "nodeid": "tests/a.py::test_a"}]


def test_timing_run_filter_precedes_pairing_and_excludes_other_runs(tmp_path: Path) -> None:
    report = _timings(
        _summary(
            tmp_path,
            [_event("START"), _event("FINISH"), _event("FINISH", run_id="other")],
            run_id="run-a",
        )
    )

    assert report["paired_count"] == 1
    assert report["issue_count"] == 0


@pytest.mark.parametrize("mode", [[], ["--failures-only"], ["--verbose"]])
def test_timing_cli_is_opt_in_for_concise_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: list[str]
) -> None:
    _summary(tmp_path, [_event("START"), _event("FINISH")])
    path = str(tmp_path / "trace.jsonl")

    assert main([*mode, path]) == 0
    ordinary = json.loads(capsys.readouterr().out)
    assert ("timings" in ordinary) is ("--verbose" in mode)
    assert main([*mode, "--include-timings", path]) == 0
    measured = json.loads(capsys.readouterr().out)
    assert measured["timings"]["paired_count"] == 1
    assert measured["timings"]["gate_evidence"] is False


def test_timing_caps_diagnostic_arrays_and_preserves_total_counts(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    for index in range(30):
        node = f"tests/t{index:02}.py::test_a"
        values = {"worker": f"gw{index:02}", "pid": index + 1}
        events.extend([_event("START", node, **values), _event("FINISH", node, **values)])
        events.append(_event("FINISH", node, run_id="unmatched", **values))
    report = _timings(_summary(tmp_path, events))

    assert report["paired_count"] == 30
    assert report["issue_count"] == 30
    assert report["limit"] == 25
    for key in ("issues", "slowest_tests", "modules", "workers"):
        assert len(cast(list[object], report[key])) == 25
    assert cast(list[dict[str, object]], report["slowest_tests"])[0]["nodeid"] == "tests/t00.py::test_a"


def test_timing_tolerates_empty_trace_and_retains_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "absent.jsonl"
    assert _timings(summarize_log(path))["paired_count"] == 0
    path.write_text("not-json" + chr(10) + "{}" + chr(10), encoding="utf-8")
    report = _timings(summarize_log(path))

    assert report["paired_count"] == 0
    assert _reasons(report) == ["parse_error"]


@pytest.mark.parametrize(
    ("setting", "expected_status", "includes_timings"),
    [("0", 0, False), ("1", 0, True), ("2", 2, False), ("", 2, False)],
)
def test_native_summary_make_owner_validates_its_timing_option(
    tmp_path: Path, setting: str, expected_status: int, includes_timings: bool
) -> None:
    result = subprocess.run(
        [
            "make", "--no-print-directory", "test-xdist-trace-summary", "UV=echo",
            f"LOG={tmp_path / 'trace.jsonl'}", "RUN_ID=timing-contract",
            f"TRACE_SUMMARY_TIMINGS={setting}",
        ],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=15,
    )

    assert result.returncode == expected_status, result.stdout + result.stderr
    assert ("--include-timings" in result.stdout) is includes_timings
    if expected_status:
        assert "TRACE_SUMMARY_TIMINGS must be 0 or 1" in result.stderr


def test_native_summary_treats_its_timing_option_as_inert_data(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-run"
    result = subprocess.run(
        [
            "make", "--no-print-directory", "test-xdist-trace-summary", "UV=echo",
            f"LOG={tmp_path / 'trace.jsonl'}", "RUN_ID=timing-contract",
            f"TRACE_SUMMARY_TIMINGS=`touch {marker}`",
        ],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=15,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "TRACE_SUMMARY_TIMINGS must be 0 or 1" in result.stderr
    assert not marker.exists(), "a rejected timing option executed a shell command"
