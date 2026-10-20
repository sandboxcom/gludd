"""Exercise literal optional PID transport through the actual native Make owner."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("value", [
    "", "12345", "0", "$(shell touch TRIPWIRE)", "`touch TRIPWIRE`",
    "'; touch TRIPWIRE; '", "123\n456",
], ids=["default", "positive", "zero", "make-expression", "backticks", "quote", "newline"])
def test_native_status_target_preserves_literal_input_without_export_or_execution(
    tmp_path: Path, environment_overrides: bool, value: str,
) -> None:
    recorder = tmp_path / "capture.py"
    recorder.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], "
        "'carrier': os.environ.get('GLUDD_ACTIVE_WORK_STATUS_PID'), "
        "'public_exported': 'ACTIVE_WORK_STATUS_PID' in os.environ}))\n",
        encoding="utf-8",
    )
    helper = f"{shlex.quote(sys.executable)} {shlex.quote(str(recorder))}"
    makefile = tmp_path / "probe.mk"
    makefile.write_text(
        f"include {ROOT / 'make' / '50-development-and-guardrails.mk'}\n",
        encoding="utf-8",
    )
    inherited = os.environ.copy()
    environment = {
        key: text for key, text in inherited.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    environment.update({
        "GLUDD_ACTIVE_WORK_STATUS_PID": "ambient-wrong",
        "ACTIVE_WORK_STATUS_PID": "ambient-wrong",
    })
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(makefile), "active-work-status",
            f"SYSTEM_PYTHON={helper}", f"ACTIVE_WORK_STATUS_PID={value}",
        ],
        cwd=tmp_path, env=environment, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "argv": ["scripts/active_work_status.py", *(["--inspect-pid", value] if value else [])],
        "carrier": value, "public_exported": False,
    }
    assert not (tmp_path / "TRIPWIRE").exists()
    assert os.environ == inherited
