"""Bind complete locked EE consumer checks to pre-publication admission."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_locked_ee_consumer_runs_before_publication_without_replacing_full_gate() -> None:
    """Inspect the actual native plan and retain complete release authority."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "_pytest-runtime-compatibility-admission"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assignments = [
        argument.removeprefix("TESTFILES=").split()
        for argument in shlex.split(result.stdout) if argument.startswith("TESTFILES=")
    ]
    assert assignments
    for cohort in ("test_ansible_ee_locked_consumer.py", "test_ansible_ee_locked_consumer_admission.py"):
        assert assignments[0].count(f"tests/unit/{cohort}") == 1
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(entry for entry in manifest["families"] if entry["id"] == "ee-locked-archive-context-consumer")
    assert family == {
        "id": "ee-locked-archive-context-consumer",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": "tests/unit/test_ansible_ee_locked_consumer.py::"
                "test_native_create_consumes_sixteen_local_metadata_fixtures_once",
        "full_gate_phase": "test",
    }
    assert manifest["full_gate_target"] == "gate" and manifest["full_gate_required"] is True
