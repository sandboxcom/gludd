"""Unit tests for scripts/floor_planner.py — the deployment-decision planner.

Branch coverage of the pure :func:`plan` core:
  - below floor            -> dispatch up to the buffered aim,
  - in band                -> dispatch 0,
  - at/over ceiling        -> dispatch 0,
  - predicted-drain        -> pre-dispatch to cover agents about to complete,
  - ceiling headroom clamp -> never exceed the ceiling,
  - input normalization    -> nonsensical inputs are coerced, never crash,
plus the CLI/JSON wrapper and its arg parsing.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MODPATH = Path(__file__).resolve().parents[2] / "scripts" / "floor_planner.py"
_spec = importlib.util.spec_from_file_location("floor_planner", _MODPATH)
assert _spec and _spec.loader
floor_planner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(floor_planner)

plan = floor_planner.plan


# --- below floor -> dispatch toward buffered aim ----------------------------
def test_below_floor_dispatches_to_buffer() -> None:
    # live 2, floor 6, target 10, ceiling 12, buffer 2 -> aim = max(8, target=10)=10
    d = plan(2, 6, 10, 12, buffer=2)
    assert d["dispatch_now"] == 8  # 10 - 2
    assert "below floor" in d["reason"]
    assert d["live"] == 2 and d["floor"] == 6


def test_below_floor_buffer_above_floor_when_target_low() -> None:
    # target == floor, so aim is driven by floor+buffer.
    d = plan(3, 6, 6, 12, buffer=2)
    assert d["dispatch_now"] == 5  # aim = 8, 8 - 3
    assert "below floor" in d["reason"]


def test_zero_live_dispatches_to_aim() -> None:
    d = plan(0, 6, 6, 12, buffer=2)
    assert d["dispatch_now"] == 8  # aim 8 - 0


# --- in band -> 0 -----------------------------------------------------------
def test_in_band_dispatches_zero() -> None:
    d = plan(10, 6, 10, 12)
    assert d["dispatch_now"] == 0
    assert "in band" in d["reason"]


def test_at_aim_exactly_zero() -> None:
    # aim = max(floor+buffer=8, target=8) = 8; live 8 -> 0
    d = plan(8, 6, 8, 12, buffer=2)
    assert d["dispatch_now"] == 0


def test_above_aim_below_ceiling_zero() -> None:
    d = plan(11, 6, 10, 12)
    assert d["dispatch_now"] == 0
    assert "in band" in d["reason"]


# --- at / over ceiling -> 0 -------------------------------------------------
def test_at_ceiling_zero() -> None:
    d = plan(12, 6, 10, 12)
    assert d["dispatch_now"] == 0
    assert "ceiling" in d["reason"]


def test_over_ceiling_zero() -> None:
    d = plan(15, 6, 10, 12)
    assert d["dispatch_now"] == 0
    assert "ceiling" in d["reason"]


def test_ceiling_headroom_limits_dispatch() -> None:
    # aim is pulled up to the ceiling (target 12 == ceiling 12); live 11 is below
    # aim by 1 and exactly 1 slot of headroom remains -> dispatch exactly 1, and
    # never pushes live over the ceiling.
    d = plan(11, 6, 12, 12, buffer=2)
    assert d["dispatch_now"] == 1  # min(need=1, headroom=1)
    assert d["live"] + d["dispatch_now"] <= d["ceiling"]


# --- predicted drain pre-dispatch -------------------------------------------
def test_predicted_drain_predispatches() -> None:
    # live 8 (in band by raw count), but 3 are old enough to drain soon.
    # effective = 5 < aim 8 -> dispatch to cover the gap, clamped by headroom.
    ages = [9999, 9999, 9999, 10, 10, 10, 10, 10]
    d = plan(8, 6, 8, 12, inflight_ages=ages, drain_age=240, buffer=2)
    # aim 8, effective_live 5 -> need 3, headroom = 12-8 = 4 -> dispatch 3
    assert d["dispatch_now"] == 3
    assert "predicted drain" in d["reason"]


def test_predicted_drain_below_floor_branch() -> None:
    # effective drops below floor -> "below floor" wording, drain noted.
    ages = [9999, 9999, 9999, 9999]  # all 4 draining
    d = plan(7, 6, 6, 12, inflight_ages=ages, drain_age=240, buffer=2)
    # effective = 3 < floor 6, aim = 8 -> need 5, headroom = 12-7 = 5 -> dispatch 5
    assert d["dispatch_now"] == 5
    assert "below floor" in d["reason"]
    assert "predicted drain" in d["reason"]


def test_young_agents_do_not_trigger_drain() -> None:
    ages = [10, 20, 30]  # none past the 240s threshold
    d = plan(8, 6, 8, 12, inflight_ages=ages, drain_age=240, buffer=2)
    assert d["dispatch_now"] == 0
    assert "predicted drain" not in d["reason"]


def test_drain_clamped_by_ceiling() -> None:
    ages = [9999] * 5
    # live 11, 5 draining -> effective 6, aim 8 -> need 2, headroom 12-11=1 -> 1
    d = plan(11, 6, 8, 12, inflight_ages=ages, drain_age=240, buffer=2)
    assert d["dispatch_now"] == 1
    assert "clamped by ceiling headroom" in d["reason"]


def test_drain_in_band_reports_effective() -> None:
    # one draining, but effective still >= aim -> 0 with drain-aware reason.
    ages = [9999, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]  # 11 live, 1 drains
    d = plan(11, 6, 8, 12, inflight_ages=ages, drain_age=240, buffer=2)
    assert d["dispatch_now"] == 0
    assert "predicted-drain" in d["reason"]


def test_none_ages_in_list_ignored() -> None:
    d = plan(8, 6, 8, 12, inflight_ages=[None, None], drain_age=240, buffer=2)  # type: ignore[list-item]
    assert d["dispatch_now"] == 0


# --- input normalization / robustness ---------------------------------------
def test_negative_live_normalized() -> None:
    d = plan(-5, 6, 10, 12)
    assert d["live"] == 0
    assert d["dispatch_now"] == 10  # treated as 0 live -> aim 10


def test_target_above_ceiling_clamped() -> None:
    d = plan(0, 6, 99, 12)
    # target clamped to ceiling 12; aim -> 12
    assert d["target"] == 12
    assert d["dispatch_now"] == 12


def test_floor_above_ceiling_clamped() -> None:
    d = plan(0, 99, 99, 12)
    assert d["floor"] == 12
    assert d["ceiling"] == 12
    assert d["dispatch_now"] == 12


def test_zero_buffer_aims_at_target() -> None:
    d = plan(0, 6, 6, 12, buffer=0)
    # aim = max(floor+0=6, target=6) = 6
    assert d["dispatch_now"] == 6


def test_decision_shape_keys() -> None:
    d = plan(2, 6, 10, 12)
    assert set(d) == {"live", "floor", "target", "ceiling", "dispatch_now", "reason"}
    assert isinstance(d["dispatch_now"], int)
    assert isinstance(d["reason"], str)


# --- CLI / JSON wrapper -----------------------------------------------------
def test_main_emits_valid_json(capsys) -> None:
    rc = floor_planner.main(
        ["--live", "2", "--floor", "6", "--target", "10", "--ceiling", "12"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["live"] == 2
    assert out["dispatch_now"] == 8


def test_main_parses_ages(capsys) -> None:
    rc = floor_planner.main(
        [
            "--live", "8", "--floor", "6", "--target", "8", "--ceiling", "12",
            "--ages", "9999,9999,9999,10",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dispatch_now"] == 3
    assert "predicted drain" in out["reason"]


def test_main_defaults_from_env(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("FLOOR_MIN", "4")
    monkeypatch.setenv("FLOOR_TARGET", "8")
    monkeypatch.setenv("FLOOR_CEILING", "10")
    rc = floor_planner.main(["--live", "0"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["floor"] == 4
    assert out["target"] == 8
    assert out["ceiling"] == 10


def test_main_bad_arg_falls_back_to_default(capsys) -> None:
    # Non-int after --live -> default 0 used, not a crash.
    rc = floor_planner.main(["--live", "notanint"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["live"] == 0


def test_main_missing_arg_value(capsys) -> None:
    rc = floor_planner.main(["--live"])  # flag with no value -> default
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["live"] == 0


def test_main_empty_ages(capsys) -> None:
    rc = floor_planner.main(["--live", "8", "--floor", "6", "--target", "8",
                             "--ceiling", "12", "--ages", ""])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dispatch_now"] == 0


def test_main_ages_with_garbage_entries(capsys) -> None:
    rc = floor_planner.main(["--live", "8", "--floor", "6", "--target", "8",
                             "--ceiling", "12", "--ages", "9999,oops,,9999"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # two valid draining ages parsed -> effective 6 < aim 8 -> dispatch 2
    assert out["dispatch_now"] == 2


def test_ages_arg_missing_value() -> None:
    # --ages as the last token with no following value -> empty list, no crash.
    assert floor_planner._ages_arg(["--ages"]) == []
