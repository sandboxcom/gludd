"""Exercise terminal Make guidance with the existing native six-pin fixture."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit import test_gate_failure_promotion as promotion


@pytest.mark.parametrize("failed_owner", ("", *promotion.STRUCTURAL_PROMOTION_NODES, "validator"))
def test_promotion_make_guidance_waits_for_all_required_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_owner: str,
) -> None:
    """Earlier manifest output cannot grant a whole-target positive."""
    native_run = subprocess.run
    results: list[subprocess.CompletedProcess[str]] = []

    def execute(
        arguments: list[str], *, cwd: Path, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        probe = tmp_path / "record.py"
        source = probe.read_text(encoding="utf-8")
        marker = 'print("PROMOTION_CHILD_TERMINAL phase=" + phase, flush=True)\n'
        assert source.count("raise SystemExit(") == 1
        probe.write_text(source.replace("raise SystemExit(", marker + "raise SystemExit("), encoding="utf-8")
        result = native_run(
            arguments, cwd=cwd, capture_output=capture_output, text=text, timeout=timeout,
        )
        results.append(result)
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(subprocess, "run", execute)
        promotion.test_promotion_checker_executes_both_structural_pins_only_after_valid_manifest(
            tmp_path, failed_owner,
        )

    assert len(results) == 1
    result = results[0]
    combined = result.stdout + result.stderr
    assert "gate_evidence=false release_ready=false" in combined
    passed = "AGENT_NEXT target=check-gate-failure-promotions outcome=passed"
    if failed_owner:
        assert passed not in combined
        assert "AGENT_NEXT target=check-gate-failure-promotions outcome=failed" in combined
        assert "repair" in combined.lower()
        assert result.returncode == 2
        if failed_owner == "validator":
            assert "phase=manifest" in combined
            assert "PROMOTION_CHILD_TERMINAL phase=tests" not in combined
        else:
            assert "phase=eight-pins" in combined
    else:
        assert result.returncode == 0
        assert result.stdout.index(passed) > result.stdout.index("PROMOTION_CHILD_TERMINAL phase=tests")
        assert "complete changed owners" in result.stdout
        assert "eight-pins" in result.stdout
