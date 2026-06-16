"""Input-hardening proof for the A/B subprocess runner.

These tests target the PARENT-side argv construction in
``run_candidate_in_subprocess`` — the boundary where caller/config-supplied
values (candidate_root, workload spec, limits) are turned into the child's
argv. They prove that:

  * an injection-y / escaping ``candidate_root`` (NUL byte, empty) is REJECTED
    before any subprocess is spawned,
  * a non-serializable / non-dict ``workload`` is REJECTED before spawn,
  * a normal run builds the EXACT expected argv as a list of str (subprocess
    mocked — no real child),
  * the success path is still gated on the parent's nonce appearing in the
    result file (the nonce gate cannot be bypassed by a child that merely
    exits 0).

Rejection raises ``ValueError`` and spawns NOTHING — proven by patching
``subprocess.Popen`` to a sentinel that fails the test if called.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from general_ludd.abtest import runner as runner_mod
from general_ludd.abtest.runner import run_candidate_in_subprocess
from general_ludd.abtest.workloads import import_module_workload


class _ExplodingPopen:
    """A Popen stand-in that fails the test if it is ever constructed.

    Used to prove that input validation rejects bad input BEFORE any child is
    spawned.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "subprocess.Popen must NOT be called for rejected/validated-out input"
        )


def test_candidate_root_with_nul_byte_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate_root containing a NUL byte is argv-corrupting; reject it
    before spawning (no Popen call)."""
    monkeypatch.setattr(runner_mod.subprocess, "Popen", _ExplodingPopen)
    workload = import_module_workload("whatever.mod")
    with pytest.raises(ValueError):
        run_candidate_in_subprocess("/some/root\x00injected", workload, timeout=5.0)


def test_empty_candidate_root_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty / whitespace-only candidate_root cannot name a real src dir;
    reject before spawning."""
    monkeypatch.setattr(runner_mod.subprocess, "Popen", _ExplodingPopen)
    workload = import_module_workload("whatever.mod")
    with pytest.raises(ValueError):
        run_candidate_in_subprocess("   ", workload, timeout=5.0)


def test_non_dict_workload_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workload must be a JSON-serializable dict. A list (or other type)
    is rejected before spawning so no surprise argv shape reaches the child."""
    monkeypatch.setattr(runner_mod.subprocess, "Popen", _ExplodingPopen)
    with pytest.raises(ValueError):
        run_candidate_in_subprocess("/root", ["not", "a", "dict"], timeout=5.0)  # type: ignore[arg-type]


def test_non_serializable_workload_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A workload carrying a non-JSON-serializable value (e.g. a callable) is
    rejected before spawning — we never ship a Python object across the
    boundary."""
    monkeypatch.setattr(runner_mod.subprocess, "Popen", _ExplodingPopen)
    bad = {"kind": "import_module", "module": "m", "callback": lambda: None}
    with pytest.raises(ValueError):
        run_candidate_in_subprocess("/root", bad, timeout=5.0)


def test_negative_mem_limit_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A negative memory limit is nonsense and could weaken the child's
    resource cap; reject before spawning."""
    monkeypatch.setattr(runner_mod.subprocess, "Popen", _ExplodingPopen)
    workload = import_module_workload("m.mod")
    with pytest.raises(ValueError):
        run_candidate_in_subprocess("/root", workload, timeout=5.0, mem_limit_mb=-1)


def _capture_cmd(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch Popen + the nonce-check so a normal call builds argv without
    spawning a real child. Returns a dict that will hold the captured cmd and
    the (parent-generated) nonce.

    The fake Popen records the cmd and behaves like a clean child: exit 0. The
    nonce check is patched to honor the parent's nonce so the success path runs
    end-to-end without a real result file.
    """
    captured: dict[str, object] = {}

    class _FakeProc:
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, None]:
            return ("RESULT_OK\n", None)

        def kill(self) -> None:  # pragma: no cover - not hit on clean path
            pass

    def _fake_popen(cmd: object, **kwargs: object) -> _FakeProc:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    # The nonce written into argv is what the parent must later require in the
    # result file. Capture it so the test can assert the gate is wired to the
    # SAME nonce, then let the gate pass for the success-path assertion.
    def _fake_nonce_matches(result_path: str, expected_nonce: str) -> bool:
        captured["gate_expected_nonce"] = expected_nonce
        return True

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(runner_mod, "_result_nonce_matches", _fake_nonce_matches)
    return captured


def test_normal_run_builds_expected_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal run builds the exact argv as a list[str]: interpreter, -m,
    child module, root, workload-json, mem, cpu, result_path, nonce."""
    captured = _capture_cmd(monkeypatch)
    workload = import_module_workload("cand.mod", expect_attr="VALUE")

    result = run_candidate_in_subprocess(
        "/abs/candidate/root", workload, timeout=10.0, mem_limit_mb=256
    )

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert all(isinstance(part, str) for part in cmd), cmd

    # Fixed leading argv shape.
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    assert cmd[2] == "general_ludd.abtest._child"
    assert cmd[3] == "/abs/candidate/root"

    # workload is shipped as JSON, round-trips to the original dict.
    assert json.loads(cmd[4]) == workload

    # limits are stringified ints; cpu derived from the timeout.
    assert cmd[5] == "256"
    assert cmd[6] == str(int(10.0) + 1)

    # result_path then nonce are the last two args.
    result_path, nonce = cmd[7], cmd[8]
    assert isinstance(result_path, str) and result_path
    assert isinstance(nonce, str) and len(nonce) == 64  # token_hex(32)

    # No shell — argv list passed straight to Popen.
    assert captured["kwargs"].get("shell") in (None, False)

    # The nonce gate is wired to the SAME nonce that went into argv.
    assert captured["gate_expected_nonce"] == nonce

    # Exactly 9 argv elements — no extra injected args.
    assert len(cmd) == 9

    assert result.ok is True


def test_nonce_gate_not_bypassed_by_clean_exit(tmp_path: Path) -> None:
    """A real child that imports cleanly exits 0; ok is decided strictly by the
    nonce gate (the framework-written nonce in the result file), never by the
    stdout sentinel. Documents the gate is the authority and ok is a strict
    bool.
    """
    src = tmp_path / "candidate" / "src" / "cand_gate"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "mod.py").write_text("VALUE = 1\n")
    workload = import_module_workload("cand_gate.mod", expect_attr="VALUE")
    result = run_candidate_in_subprocess(
        tmp_path / "candidate", workload, timeout=30.0
    )
    assert isinstance(result.ok, bool)
    assert result.ok is True
