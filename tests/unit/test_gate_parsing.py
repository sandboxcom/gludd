"""Smoke tests for the gate-log parsing module."""

from __future__ import annotations

from pathlib import Path

from general_ludd.gate.parsing import discover_gate_logs, parse_gate_log


def _write_log(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_gate_log_returns_gate_run(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    run = parse_gate_log(log)
    assert run.terminal == "PASSED"
    assert run.total_lines == 3
    assert run.resume_batches == 0


def test_discover_gate_logs_filters_non_gate_files(tmp_path: Path) -> None:
    _write_log(tmp_path, "gate-20261017000000-00000000.log", "x\n")
    _write_log(tmp_path, "gate-pytest-20261017.log", "x\n")
    _write_log(tmp_path, "notes.txt", "x\n")
    found = discover_gate_logs(tmp_path)
    assert [p.name for p in found] == ["gate-20261017000000-00000000.log"]
