"""Behavioral tests for the local gate runner."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "gate_local.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_local_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_local_source_is_valid_utf8() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    compile(source, str(SCRIPT_PATH), "exec")


def test_run_reports_success_and_failure(tmp_path: Path) -> None:
    module = _load_script()

    ok, completed = module.run(
        (sys.executable, "-c", "print('healthy')"),
        log=tmp_path / "success.log",
    )
    assert ok is True
    assert completed is not None
    assert completed.returncode == 0
    assert "healthy" in (tmp_path / "success.log").read_text(encoding="utf-8")

    ok, completed = module.run(
        (sys.executable, "-c", "raise SystemExit(3)"),
        log=tmp_path / "failure.log",
    )
    assert ok is False
    assert completed is not None
    assert completed.returncode == 3


def test_run_reports_timeout(tmp_path: Path) -> None:
    module = _load_script()
    log_path = tmp_path / "timeout.log"

    ok, completed = module.run(
        (sys.executable, "-c", "import time; time.sleep(1)"),
        log=log_path,
        timeout=0.01,
    )

    assert ok is False
    assert completed is None
    assert "TIMEOUT" in log_path.read_text(encoding="utf-8")


def test_main_stops_after_first_failed_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_script()
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, log=None, timeout=module.DEFAULT_TIMEOUT):
        del log, timeout
        normalized = tuple(command)
        calls.append(normalized)
        return normalized != ("fail",), subprocess.CompletedProcess(
            normalized,
            0 if normalized != ("fail",) else 1,
        )

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(
        module,
        "PHASES",
        (
            ("first", ("pass",)),
            ("second", ("fail",)),
            ("third", ("never",)),
        ),
    )
    status_path = tmp_path / ".gate-status"

    assert module.main(gate_file=status_path) == 1
    assert calls == [("pass",), ("fail",)]
    status = status_path.read_text(encoding="utf-8")
    assert "first: PASS" in status
    assert "second: FAIL" in status
    assert "third" not in status
