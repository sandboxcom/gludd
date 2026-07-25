"""Regression coverage for the bounded E2E runner Make target."""

from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    remaining = content[start:]
    return remaining.split("\n\n", 1)[0]


def test_e2e_runner_uses_unique_basetemp() -> None:
    body = _target_body("test-e2e")
    assert 'BT="/tmp/gludd-e2e-' in body
    assert '--basetemp=$$BT' in body


def test_e2e_runner_marks_nested_execution() -> None:
    body = _target_body("test-e2e")
    assert "GLUDD_E2E_ACTIVE=1" in body


def test_e2e_runner_has_per_test_timeout_and_cleanup() -> None:
    body = _target_body("test-e2e")
    assert "--timeout=" in body
    assert 'rm -rf "$$BT"' in body
    assert "exit $$RC" in body


def test_e2e_marker_is_registered() -> None:
    config = (MAKEFILE.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"e2e:' in config


def test_e2e_runner_has_suite_watchdog_bounds() -> None:
    body = _target_body("test-e2e")
    assert "run-watched" in body
    assert "E2E_MAX_SECS" in body
    assert "E2E_STALL_SECS" in body
