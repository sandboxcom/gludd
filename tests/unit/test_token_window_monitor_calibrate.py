"""Unit tests for the token-window monitor's calibrate path + budget precedence.

The monitor is a token-sum PROXY: the real API rate-limit headers are not
persisted in the transcript (proven via ``--probe``), so the only way to make
its reported percentage match reality is to ANCHOR the budget (denominator) to
an operator-supplied real reading. These tests pin that anchoring contract
(``--calibrate <pct>``) and the ``budget()`` file-precedence/fallback rules,
which were shipped without coverage.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The monitor lives under scripts/ (not an installed package), so load it by path.
_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "token_window_monitor.py"
)
_spec = importlib.util.spec_from_file_location("token_window_monitor", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
twm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(twm)


@pytest.fixture
def budget_file(tmp_path, monkeypatch):
    """Redirect the module's BUDGET_FILE at a throwaway path."""
    bf = tmp_path / "budget"
    monkeypatch.setattr(twm, "BUDGET_FILE", bf)
    return bf


# --------------------------------------------------------------------------- #
# --calibrate <pct>
# --------------------------------------------------------------------------- #
def test_calibrate_valid_pct_writes_anchored_budget(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "spend_last_5h", lambda: 100_000)
    rc = twm.main(["--calibrate", "80"])
    assert rc == 0
    # budget = round(100000 / 0.80) = 125000
    assert budget_file.read_text().strip() == "125000"


def test_calibrate_rejects_pct_zero_or_negative(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "spend_last_5h", lambda: 100_000)
    assert twm.main(["--calibrate", "0"]) == 1
    assert twm.main(["--calibrate", "-5"]) == 1
    assert not budget_file.exists()  # never written


def test_calibrate_rejects_pct_over_100(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "spend_last_5h", lambda: 100_000)
    assert twm.main(["--calibrate", "101"]) == 1
    assert not budget_file.exists()


def test_calibrate_missing_arg_is_usage_error(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "spend_last_5h", lambda: 100_000)
    assert twm.main(["--calibrate"]) == 1
    assert not budget_file.exists()


def test_calibrate_zero_spend_fails_without_writing(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "spend_last_5h", lambda: 0)
    assert twm.main(["--calibrate", "90"]) == 1
    assert not budget_file.exists()


# --------------------------------------------------------------------------- #
# budget() precedence + fallbacks
# --------------------------------------------------------------------------- #
def test_budget_prefers_file_over_default(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "DEFAULT_BUDGET", 316_000_000)
    budget_file.write_text("250000")
    assert twm.budget() == 250_000


def test_budget_ignores_zero_or_negative_file(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "DEFAULT_BUDGET", 316_000_000)
    budget_file.write_text("0")
    assert twm.budget() == 316_000_000
    budget_file.write_text("-100")
    assert twm.budget() == 316_000_000


def test_budget_ignores_malformed_file(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "DEFAULT_BUDGET", 316_000_000)
    budget_file.write_text("not_a_number")
    assert twm.budget() == 316_000_000


def test_budget_uses_default_when_file_missing(budget_file, monkeypatch):
    monkeypatch.setattr(twm, "DEFAULT_BUDGET", 316_000_000)
    assert not budget_file.exists()
    assert twm.budget() == 316_000_000
