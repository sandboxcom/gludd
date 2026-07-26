"""Regression tests for namespace-safe adaptive full-gate termination."""

from __future__ import annotations

from pathlib import Path

from scripts.kill_owned_gate import ProcessRecord, owned_adaptive_gate_records


def test_gate_kill_selects_adaptive_full_gate_only() -> None:
    root = Path("/Users/shawnwilson/gludd")
    records = [
        ProcessRecord(
            12327,
            12322,
            42,
            f"{root}/.venv/bin/python scripts/adaptive_test.py tests/ -q "
            "--cov=general_ludd --cov-fail-under=85",
        ),
        ProcessRecord(
            43919,
            43913,
            3600,
            f"{root}/.venv/bin/python scripts/audit_coverage.py --source=src/general_ludd",
        ),
        ProcessRecord(
            54293,
            43919,
            120,
            f"{root}/.venv/bin/python -m pytest {root}/tests/e2e/test_config_workflows.py "
            "--cov=src/general_ludd --cov-append",
        ),
    ]

    selected = owned_adaptive_gate_records(records, project_root=root)

    assert [record.pid for record in selected] == [12327]
