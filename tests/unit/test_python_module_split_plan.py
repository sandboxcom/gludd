"""Structural contract for splitting oversized production Python modules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "quality" / "python-module-split-plan.md"

EXPECTED_PATHS = {
    "scripts/agent_watchdog.py",
    "scripts/test_hook_runtime.py",
    "src/general_ludd/cli.py",
    "src/general_ludd/daemon.py",
    "src/general_ludd/db/repository.py",
    "src/general_ludd/event_loop/loop.py",
    "src/general_ludd/models/gateway.py",
    "src/general_ludd/pricing_intel/sources.py",
    "src/general_ludd/self_improve/codex_comparison.py",
    "src/general_ludd/self_improve/managed_runner.py",
    "src/general_ludd/self_improve/runtime.py",
}


def _ledger_rows() -> list[list[str]]:
    """Return normalized rows from the exact source-module ledger."""
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## Exact 11-module execution ledger")
    end = text.index("\n## ", start + 3)
    rows: list[list[str]] = []
    for line in text[start:end].splitlines():
        if not line.startswith("| `"):
            continue
        rows.append([cell.strip().strip("`") for cell in line.strip("|").split("|")])
    return rows


def test_plan_assigns_every_oversized_python_file_an_executable_contract() -> None:
    """Every violation has extraction, compatibility, risk, proof, and rollback."""
    assert PLAN.is_file()
    rows = _ledger_rows()
    assert len(rows) == 11
    assert all(len(row) == 9 for row in rows)

    by_path = {row[0]: row for row in rows}
    assert len(by_path) == len(rows), "module paths must be unique"
    assert set(by_path) == EXPECTED_PATHS

    for path, row in by_path.items():
        extraction, facade, state_cycle, tests, coverage, rollback, wave, owner = row[1:]
        assert all((extraction, facade, state_cycle, tests, coverage, rollback, wave, owner))
        assert ".py" in extraction, path
        assert "re-export" in facade.lower() or "entrypoint" in facade.lower(), path
        assert "cycle" in state_cycle.lower(), path
        assert "make test" in tests.lower(), path
        assert ">=85%" in coverage and ">=75%" in coverage, path
        assert "revert" in rollback.lower(), path
        assert wave.startswith("W"), path
        assert owner in {"A", "B", "serial"}, path
        assert not {"tbd", "exempt", "grandfather"} & set(" ".join(row).lower().split()), path


def test_plan_pins_automated_compatibility_and_disjoint_wave_gates() -> None:
    """The plan makes import compatibility and merge sequencing mechanical."""
    text = PLAN.read_text(encoding="utf-8")
    required_phrases = {
        "AST public-surface snapshot",
        "importlib identity matrix",
        "signature parity",
        "monkeypatch target inventory",
        "no extracted module imports its facade",
        "one oversized module per commit",
        "Wave W1",
        "Wave W2",
        "make test-files",
        "make typecheck-scope",
        "make coverage-files",
        "make lint-files",
        "make check-file-line-limits",
    }
    assert all(phrase in text for phrase in required_phrases)
    assert "pricing_intel/sources.py` (owner A)" in text
    assert "test_hook_runtime.py` (owner B)" in text
    assert "db/repository.py` (owner A)" in text
    assert "agent_watchdog.py` (owner B)" in text
    assert "No two owners in a wave" in text
