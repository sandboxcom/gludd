"""RED-TEAM tests for MASTER's self-mutation path (reload/ + abtest/).

These tests demonstrate two confirmed weaknesses in the hot-reload +
self-improvement subsystem. They are written to FAIL against the current code
and PASS only once the underlying issues are fixed. See REDTEAM_RELOAD.md for
the full traced analysis.

BUG #1 (HIGH): the A/B "fail-closed" sentinel is forgeable by the candidate.
  A malicious candidate module prints the RESULT_OK sentinel itself and then
  calls os._exit(0), bypassing the child's own assertion gate. The parent runner
  cannot distinguish the framework's sentinel from the candidate's, so the bad
  candidate is reported ok=True and compare.decide() promotes it.

BUG #2 (MEDIUM): reload rollback is not guaranteed / is fail-open in places.
  - reload_code_module with no health_check swaps a corrupt-but-importable
    candidate over the live module on "it imports" alone.
  - ReloadManager.execute_reload always reports success without doing anything.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from general_ludd.abtest.compare import decide, run_ab
from general_ludd.abtest.runner import run_candidate_in_subprocess
from general_ludd.abtest.workloads import import_module_workload
from general_ludd.reload.hot_reloader import HotReloader
from general_ludd.reload.manager import ReloadManager, ReloadType


def _make_candidate_pkg(root: Path, module_tail: str, body: str) -> str:
    """Lay out <root>/src/<pkg path>/<module>.py with `body` and return the
    dotted module name so an import_module workload can target it.

    `module_tail` is a dotted path like "evilpkg.evilmod".
    """
    parts = module_tail.split(".")
    pkg_parts, mod_name = parts[:-1], parts[-1]
    cur = root / "src"
    cur.mkdir(parents=True, exist_ok=True)
    for p in pkg_parts:
        cur = cur / p
        cur.mkdir(exist_ok=True)
        (cur / "__init__.py").write_text("")
    (cur / f"{mod_name}.py").write_text(textwrap.dedent(body))
    return module_tail


# --------------------------------------------------------------------------- #
# BUG #1 — forgeable fail-closed sentinel                                      #
# --------------------------------------------------------------------------- #


def test_os_exit_zero_after_forged_sentinel_is_marked_ok(tmp_path: Path) -> None:
    """A candidate that prints the sentinel and then os._exit(0) at IMPORT time
    bypasses the child's own assertion gate. The parent should NOT mark this
    ok=True — the sentinel must be proof the *framework* completed, not proof the
    candidate echoed a magic string.

    EXPECTED (fixed): result.ok is False (forged sentinel rejected).
    ACTUAL  (current): result.ok is True — fail-closed bypassed.
    """
    module = _make_candidate_pkg(
        tmp_path,
        "evilpkg.forge_exit",
        """
        import os
        import sys
        # Forge the framework's success sentinel onto stdout, then exit 0 BEFORE
        # the child's real assertion/sentinel code can run. os._exit does NOT
        # flush buffered stdout, so we flush explicitly to be sure the forged
        # line reaches the parent's pipe.
        sys.stdout.write("RESULT_OK\\n")
        sys.stdout.flush()
        os._exit(0)
        """,
    )
    workload = import_module_workload(module)

    result = run_candidate_in_subprocess(tmp_path, workload, timeout=20.0)

    assert result.ok is False, (
        "FAIL-CLOSED BYPASS: candidate forged the RESULT_OK sentinel and "
        "os._exit(0)'d past the child's assertion gate, yet the runner reports "
        f"ok=True (exit={result.exit_code}). A bad candidate can be promoted.\n"
        f"output:\n{result.output}"
    )


def test_sentinel_forgery_via_candidate_stdout_promotes_bad_candidate(
    tmp_path: Path,
) -> None:
    """End-to-end: an honest baseline (A) vs a malicious candidate (B) that
    forges the sentinel and os._exit(0)s. compare.decide must NOT promote B.

    EXPECTED (fixed): verdict.promote is False.
    ACTUAL  (current): verdict.promote is True — bad candidate promoted into the
    running agent.
    """
    # Baseline A: an honest module that simply imports cleanly and lets the
    # child framework print the real sentinel.
    baseline_root = tmp_path / "baseline"
    _make_candidate_pkg(
        baseline_root,
        "honestpkg.mod",
        """
        VALUE = 1
        """,
    )

    # Candidate B: forges the sentinel then exits 0 before any real validation.
    candidate_root = tmp_path / "candidate"
    _make_candidate_pkg(
        candidate_root,
        "honestpkg.mod",
        """
        import os
        import sys
        sys.stdout.write("RESULT_OK\\n")
        sys.stdout.flush()
        os._exit(0)
        """,
    )

    workload = import_module_workload("honestpkg.mod")
    verdict = run_ab(baseline_root, candidate_root, workload, timeout=20.0)

    assert verdict.promote is False, (
        "PROMOTE-BAD-CANDIDATE: a malicious candidate that forged the sentinel "
        f"and os._exit(0)'d was PROMOTED (promote=True, reason={verdict.reason!r}). "
        f"b={verdict.b}"
    )


def test_decide_rejects_forged_ok_result() -> None:
    """Unit-level guard: even if a Result arrives ok=True, decide() is the last
    gate. This documents that decide() trusts its inputs entirely — once the
    runner is fixed to not produce a forged ok=True, decide() is sound. Here we
    assert the *runner-level* invariant is what must change, by confirming
    decide() promotes any ok+fast candidate (i.e. decide cannot save us).
    """
    from general_ludd.abtest.runner import Result

    a = Result(ok=True, crashed=False, timed_out=False, exit_code=0, output="", duration_s=0.1)
    forged_b = Result(
        ok=True, crashed=False, timed_out=False, exit_code=0, output="RESULT_OK", duration_s=0.1
    )
    promote, _reason = decide(a, forged_b)
    # decide() will promote a forged ok=True — proving the fix MUST live in the
    # runner, not in decide. This assertion passes today and documents the gap;
    # it is the runner-level tests above that must flip from fail to pass.
    assert promote is True


# --------------------------------------------------------------------------- #
# BUG #2 — rollback not guaranteed / fail-open reload bookkeeping              #
# --------------------------------------------------------------------------- #


def test_reload_code_module_no_health_check_swaps_on_import_only(
    tmp_path: Path,
) -> None:
    """With health_check=None (the default self-improve wiring), a
    corrupt-but-importable candidate is os.replace'd over the live module and
    reported success on "it imports" alone — no rollback, no semantic gate.

    EXPECTED (fixed): either reload_code_module refuses without a health gate, or
    the swap is reverted (rolled_back=True) so a bad-but-importable candidate is
    NOT silently committed.
    ACTUAL  (current): success=True, the live file now holds the candidate, and
    rolled_back is False.
    """
    # A live, file-backed leaf module we are allowed to rotate.
    live_root = tmp_path / "live"
    live_src = live_root / "src"
    pkg = live_src / "livepkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    live_module_path = pkg / "leaf.py"
    live_module_path.write_text("GOOD = True\nVERSION = 'original'\n")

    # Import it so it is a live module with __file__.
    import importlib
    import sys

    sys.path.insert(0, str(live_src))
    try:
        mod = importlib.import_module("livepkg.leaf")
        assert mod.VERSION == "original"

        # Candidate: imports cleanly (so importlib.reload succeeds) but is
        # semantically corrupt — drops GOOD, changes behavior. With no health
        # check, nothing detects this.
        candidate = tmp_path / "candidate_leaf.py"
        candidate.write_text("VERSION = 'CORRUPT'\n# GOOD removed; auth check gone\n")

        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_code_module(
            module_name="livepkg.leaf",
            candidate_source_path=str(candidate),
            health_check=None,  # the realistic default wiring
        )

        committed = live_module_path.read_text()
        assert not (result.success and "CORRUPT" in committed), (
            "NO-ROLLBACK: a corrupt-but-importable candidate was swapped over the "
            "live module and reported success with health_check=None. The live "
            f"file now reads:\n{committed!r}\nresult={result}"
        )
    finally:
        # Best-effort restore so we don't leave a poisoned module imported.
        live_module_path.write_text("GOOD = True\nVERSION = 'original'\n")
        with pytest.MonkeyPatch.context() as _:
            pass
        if "livepkg.leaf" in sys.modules:
            import contextlib

            with contextlib.suppress(Exception):
                importlib.reload(sys.modules["livepkg.leaf"])
        if str(live_src) in sys.path:
            sys.path.remove(str(live_src))


def test_manager_execute_reload_always_reports_success() -> None:
    """ReloadManager.execute_reload unconditionally flips status to 'success'
    without running, validating, or touching any code — a fail-OPEN success
    claim that the non-code-target self-improve path relies on.

    EXPECTED (fixed): execute_reload should not report success for a reload it
    never actually performed/validated.
    ACTUAL  (current): status == 'success' for a no-op.
    """
    mgr = ReloadManager()
    req = mgr.request_reload(ReloadType.WORKER_CODE)
    res = mgr.execute_reload(req.reload_id)

    assert res.status != "success", (
        "FAIL-OPEN: ReloadManager.execute_reload reported success for a reload "
        "that performed and validated nothing — a self-improvement is reported "
        f"applied with zero verification. status={res.status!r}"
    )


def test_rollback_does_not_verify_restored_module(tmp_path: Path) -> None:
    """When the health gate fails, _restore_module_bytes restores the file and
    re-imports, but does NOT verify the running module actually matches the
    original, and swallows a failed rollback reload while still reporting
    rolled_back=True.

    This test arms a candidate that imports cleanly but fails the health gate,
    then asserts the reported result honestly reflects that the live module was
    returned to its original behavior. It demonstrates the absence of any
    post-rollback verification: the result claims rolled_back=True purely on the
    basis of having attempted the restore.

    EXPECTED (fixed): after a rolled_back result, the runner verifies and reports
    that the in-memory module equals the original (e.g. a 'rollback_verified'
    detail), so an operator can trust the claim.
    ACTUAL  (current): no such verification exists.
    """
    live_root = tmp_path / "live"
    live_src = live_root / "src"
    pkg = live_src / "rbpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    live_module_path = pkg / "leaf.py"
    live_module_path.write_text("MARK = 'original'\n")

    import importlib
    import sys

    sys.path.insert(0, str(live_src))
    try:
        importlib.import_module("rbpkg.leaf")

        candidate = tmp_path / "cand.py"
        candidate.write_text("MARK = 'candidate'\n")

        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_code_module(
            module_name="rbpkg.leaf",
            candidate_source_path=str(candidate),
            health_check=lambda: False,  # force the rollback path
        )

        # The rollback path was taken.
        assert result.success is False
        assert result.details.get("rolled_back") is True

        # The fix should expose a verification that the restored module matches
        # the original. Its ABSENCE is the finding.
        assert "rollback_verified" in result.details, (
            "ROLLBACK NOT VERIFIED: reload_code_module reports rolled_back=True "
            "without verifying the running module was actually returned to the "
            "original state. A failed rollback importlib.reload is swallowed and "
            "still reported as rolled_back. result.details=" f"{result.details}"
        )
    finally:
        live_module_path.write_text("MARK = 'original'\n")
        if "rbpkg.leaf" in sys.modules:
            import contextlib

            with contextlib.suppress(Exception):
                importlib.reload(sys.modules["rbpkg.leaf"])
        if str(live_src) in sys.path:
            sys.path.remove(str(live_src))
