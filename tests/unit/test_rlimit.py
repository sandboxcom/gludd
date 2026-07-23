"""Tests for the shared best-effort RLIMIT helper (general_ludd.system.rlimit).

These are fast + coreutils-free: they exercise the fail-open / clamping logic by
patching the ``resource`` module, and assert the abtest child still delegates to
the shared helper (the refactor is behavior-preserving).
"""

from __future__ import annotations

import sys
import types

import pytest

from general_ludd.system import rlimit


def test_apply_limits_noop_when_resource_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``resource`` module (e.g. Windows) => silent no-op, never raises."""
    monkeypatch.setitem(sys.modules, "resource", None)  # import resource -> ImportError
    # Must not raise even with positive limits requested.
    rlimit.apply_limits(128, 5)


def _fake_resource(recorder: list[tuple[str, tuple[int, int]]]) -> types.SimpleNamespace:
    RLIM_INFINITY = -1

    def getrlimit(which: int) -> tuple[int, int]:
        return (RLIM_INFINITY, RLIM_INFINITY)

    def setrlimit(which: int, limits: tuple[int, int]) -> None:
        recorder.append((str(which), limits))

    return types.SimpleNamespace(
        RLIMIT_AS="AS",
        RLIMIT_CPU="CPU",
        RLIM_INFINITY=RLIM_INFINITY,
        getrlimit=getrlimit,
        setrlimit=setrlimit,
    )


def test_apply_limits_sets_both_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder: list[tuple[str, tuple[int, int]]] = []
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(recorder))
    rlimit.apply_limits(64, 7)
    kinds = {k for k, _ in recorder}
    assert kinds == {"AS", "CPU"}
    as_limit = next(v for k, v in recorder if k == "AS")
    assert as_limit == (64 * 1024 * 1024, 64 * 1024 * 1024)
    cpu_limit = next(v for k, v in recorder if k == "CPU")
    assert cpu_limit == (7, 7)


def test_apply_limits_cpu_budget_is_relative_to_current_process_time(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder: list[tuple[str, tuple[int, int]]] = []

    def getrlimit(which: int) -> tuple[int, int]:
        return (-1, -1)

    def setrlimit(which: int, limits: tuple[int, int]) -> None:
        recorder.append((str(which), limits))

    def getrusage(which: int) -> types.SimpleNamespace:
        assert which == "SELF"
        return types.SimpleNamespace(ru_utime=41.2, ru_stime=1.1)

    fake = types.SimpleNamespace(
        RLIMIT_CPU="CPU",
        RLIMIT_AS="AS",
        RLIM_INFINITY=-1,
        RUSAGE_SELF="SELF",
        getrlimit=getrlimit,
        setrlimit=setrlimit,
        getrusage=getrusage,
    )
    monkeypatch.setitem(sys.modules, "resource", fake)

    rlimit.apply_limits(0, 7)

    assert recorder == [("CPU", (50, 50))]


def test_apply_limits_clamps_to_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requested cap above the inherited hard limit is clamped down (no EPERM)."""
    recorder: list[tuple[str, tuple[int, int]]] = []
    hard_as = 10 * 1024 * 1024  # 10 MiB hard cap

    def getrlimit(which: int) -> tuple[int, int]:
        if which == "AS":
            return (hard_as, hard_as)
        return (100, 100)

    def setrlimit(which: int, limits: tuple[int, int]) -> None:
        recorder.append((str(which), limits))

    fake = types.SimpleNamespace(
        RLIMIT_AS="AS",
        RLIMIT_CPU="CPU",
        RLIM_INFINITY=-1,
        getrlimit=getrlimit,
        setrlimit=setrlimit,
    )
    monkeypatch.setitem(sys.modules, "resource", fake)
    rlimit.apply_limits(1024, 9999)  # request WAY above the hard caps
    as_limit = next(v for k, v in recorder if k == "AS")
    # BOTH soft and hard clamped to min(request, hard) == hard_as (soft > hard
    # would raise ValueError and apply nothing).
    assert as_limit == (hard_as, hard_as)
    cpu_limit = next(v for k, v in recorder if k == "CPU")
    assert cpu_limit == (100, 100)


def test_apply_limits_skips_nonpositive(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder: list[tuple[str, tuple[int, int]]] = []
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(recorder))
    rlimit.apply_limits(0, 0)
    assert recorder == []


def test_apply_limits_swallows_setrlimit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sandbox that forbids setrlimit must not crash the caller."""

    def getrlimit(which: int) -> tuple[int, int]:
        return (-1, -1)

    def setrlimit(which: int, limits: tuple[int, int]) -> None:
        raise OSError("operation not permitted")

    fake = types.SimpleNamespace(
        RLIMIT_AS="AS",
        RLIMIT_CPU="CPU",
        RLIM_INFINITY=-1,
        getrlimit=getrlimit,
        setrlimit=setrlimit,
    )
    monkeypatch.setitem(sys.modules, "resource", fake)
    rlimit.apply_limits(64, 7)  # must not raise


def test_child_delegates_to_shared_helper() -> None:
    """The abtest child keeps its local name but now calls the shared helper."""
    from general_ludd.abtest import _child

    assert _child.apply_limits is rlimit.apply_limits
    # _apply_limits wrapper still exists with the historical signature.
    assert callable(_child._apply_limits)
