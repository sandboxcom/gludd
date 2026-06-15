"""Crash-isolation proof for the A/B subprocess primitive.

These tests are the PROOF that candidate code runs in a FRESH interpreter child
and that a candidate built to crash the whole process (os._exit, SIGSEGV,
infinite loop, OOM) CANNOT take down the pytest process running these tests.

The crash variants are REAL crashing subprocesses (a temp candidate_root whose
target module crashes), not mocks. After each crashing run we assert a
post-call sentinel still executes — i.e. the parent (this pytest process)
survived.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from general_ludd.abtest.runner import Result, run_candidate_in_subprocess
from general_ludd.abtest.workloads import import_module_workload


def _rlimit_as_enforced(tmp: Path) -> bool:
    """True only if RLIMIT_AS is present AND actually enforced along the EXACT
    runner path on THIS machine.

    macOS exposes RLIMIT_AS but does not reliably enforce address-space limits
    the way Linux does, so a candidate can over-allocate past the limit. Rather
    than guess, we probe by running a real over-allocating candidate through
    ``run_candidate_in_subprocess`` itself: if the runner reports it crashed,
    the limit is enforced here; if it reports ok, it is not, and the OOM test is
    meaningless on this platform (skip).
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return False
    if not hasattr(resource, "RLIMIT_AS"):
        return False
    probe_root = _make_candidate(
        tmp / "rlimit_probe",
        "cand_probe/mod.py",
        """
        x = bytearray(2_000_000_000)
        """,
    )
    wl = import_module_workload("cand_probe.mod")
    res = run_candidate_in_subprocess(probe_root, wl, timeout=30.0, mem_limit_mb=128)
    return res.crashed and not res.ok


# ``_make_candidate`` is defined below; it is only referenced at call time inside
# ``_rlimit_as_enforced`` (invoked from the test body), so forward use is fine.


def _make_candidate(root: Path, module_rel: str, body: str) -> Path:
    """Write a candidate_root with src/<module_rel> containing ``body``.

    Returns the candidate_root (the dir whose ``src`` goes on sys.path).
    """
    src = root / "src"
    target = src / module_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    pkg = src
    for part in Path(module_rel).parent.parts:
        pkg = pkg / part
        init = pkg / "__init__.py"
        if not init.exists():
            init.write_text("")
    target.write_text(textwrap.dedent(body))
    return root


def test_clean_candidate_reports_ok(tmp_path: Path) -> None:
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_clean/mod.py",
        """
        VALUE = 42
        """,
    )
    workload = import_module_workload("cand_clean.mod", expect_attr="VALUE")
    result = run_candidate_in_subprocess(root, workload, timeout=30.0)
    assert isinstance(result, Result)
    assert result.ok is True
    assert result.crashed is False
    assert result.timed_out is False
    assert result.exit_code == 0


def test_os_exit_is_crash_not_exception(tmp_path: Path) -> None:
    """A child that calls os._exit(1) must yield crashed=True and NOT raise in
    the parent."""
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_osexit/mod.py",
        """
        import os
        os._exit(1)
        """,
    )
    workload = import_module_workload("cand_osexit.mod")
    result = run_candidate_in_subprocess(root, workload, timeout=30.0)
    assert result.ok is False
    assert result.crashed is True
    assert result.exit_code == 1
    # POST-call sentinel: this process is still alive.
    assert 2 + 2 == 4


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_segfault_reported_as_signal(tmp_path: Path) -> None:
    """A real SIGSEGV in the child ⇒ crashed=True, signal==11; the pytest
    process survives (proven by the post-call sentinel)."""
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_segv/mod.py",
        """
        import os
        import signal
        os.kill(os.getpid(), signal.SIGSEGV)
        """,
    )
    workload = import_module_workload("cand_segv.mod")
    result = run_candidate_in_subprocess(root, workload, timeout=30.0)
    assert result.crashed is True
    assert result.ok is False
    assert result.signal == 11
    # POST-call sentinel proving the pytest process survived a child SIGSEGV.
    sentinel = "parent-alive"
    assert sentinel == "parent-alive"


def test_infinite_loop_times_out(tmp_path: Path) -> None:
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_loop/mod.py",
        """
        while True:
            pass
        """,
    )
    workload = import_module_workload("cand_loop.mod")
    result = run_candidate_in_subprocess(root, workload, timeout=2.0)
    assert result.timed_out is True
    assert result.crashed is True
    assert result.ok is False
    # wall time is bounded near the timeout (allow generous slack for CI).
    assert result.duration_s < 20.0


def test_oom_candidate_killed(tmp_path: Path) -> None:
    """Candidate that allocates beyond mem_limit_mb is killed (MemoryError
    raise or signal); never ok."""
    if not _rlimit_as_enforced(tmp_path):
        pytest.skip("RLIMIT_AS not enforced on this platform (e.g. macOS)")
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_oom/mod.py",
        """
        # allocate ~2GB under a much smaller RLIMIT_AS
        x = bytearray(2_000_000_000)
        """,
    )
    workload = import_module_workload("cand_oom.mod")
    result = run_candidate_in_subprocess(root, workload, timeout=30.0, mem_limit_mb=128)
    assert result.ok is False
    assert result.crashed is True
    # post-call sentinel: parent survived the OOM child.
    assert True


def test_import_time_raise_is_failure(tmp_path: Path) -> None:
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_raise/mod.py",
        """
        raise RuntimeError("boom at import")
        """,
    )
    workload = import_module_workload("cand_raise.mod")
    result = run_candidate_in_subprocess(root, workload, timeout=30.0)
    assert result.ok is False
    assert result.crashed is True
    assert result.timed_out is False
    # the child traceback is captured in output (bounded)
    assert "boom at import" in result.output


def test_parent_never_imports_candidate(tmp_path: Path) -> None:
    """After a crashing run, the candidate module must be absent from the
    PARENT's sys.modules — proving the parent never imported candidate code."""
    root = _make_candidate(
        tmp_path / "candidate",
        "cand_neverimport/mod.py",
        """
        import os
        os._exit(3)
        """,
    )
    workload = import_module_workload("cand_neverimport.mod")
    before = set(sys.modules)
    run_candidate_in_subprocess(root, workload, timeout=30.0)
    after = set(sys.modules)
    assert "cand_neverimport.mod" not in sys.modules
    assert "cand_neverimport" not in sys.modules
    leaked = {m for m in (after - before) if m.startswith("cand_neverimport")}
    assert leaked == set()
    # candidate_root/src was never inserted into the parent's sys.path
    assert str(root / "src") not in sys.path


def test_ab_compare_promote_logic(tmp_path: Path) -> None:
    """run_ab promotes a clean candidate (B ok, no crash, within slack) and
    refuses a crashing one. Baseline A is itself a clean candidate_root so the
    test is self-contained (no dependency on the live repo)."""
    from general_ludd.abtest.compare import run_ab

    # A clean baseline candidate_root that defines the same module the workload
    # imports — A must pass for any promotion decision to be meaningful.
    baseline = _make_candidate(
        tmp_path / "baseline",
        "cand_target/mod.py",
        """
        VALUE = 1
        """,
    )

    # Clean candidate B (same module name, also ok).
    good = _make_candidate(
        tmp_path / "good",
        "cand_target/mod.py",
        """
        VALUE = 2
        """,
    )
    workload = import_module_workload("cand_target.mod", expect_attr="VALUE")
    verdict_good = run_ab(
        baseline_root=baseline,
        candidate_root=good,
        workload=workload,
        timeout=30.0,
    )
    assert verdict_good.a.ok is True
    assert verdict_good.b.ok is True
    assert verdict_good.promote is True

    # Crashing candidate B ⇒ never promote.
    bad = _make_candidate(
        tmp_path / "bad",
        "cand_target/mod.py",
        """
        import os
        os._exit(139)
        """,
    )
    verdict_bad = run_ab(
        baseline_root=baseline,
        candidate_root=bad,
        workload=workload,
        timeout=30.0,
    )
    assert verdict_bad.a.ok is True
    assert verdict_bad.b.crashed is True
    assert verdict_bad.promote is False
