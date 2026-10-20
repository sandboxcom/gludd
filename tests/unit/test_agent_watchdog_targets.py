"""Exercise agent watchdog policy through the actual native Make recipes."""

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
@pytest.mark.parametrize(
    ("target", "variable", "operation"),
    [
        ("watchdog-start", "WATCHDOG_START_VALIDATE_ONLY", "--start"),
        ("watchdog-stop", "WATCHDOG_VALIDATE_ONLY", "--stop"),
        ("agent-watchdog-stop", "WATCHDOG_VALIDATE_ONLY", "--stop"),
        ("watchdog-auto", "WATCHDOG_AUTO_VALIDATE_ONLY", "--start"),
    ],
)
@pytest.mark.parametrize("value", [None, "0", "1", "", "0 1", "$(shell touch TRIPWIRE)", "`touch TRIPWIRE`"])
def test_native_agent_watchdog_targets_use_literal_policy(
    tmp_path: Path, environment_overrides: bool, target: str, variable: str, operation: str, value: str | None,
) -> None:
    recorder = tmp_path / "capture.py"
    recorder.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'exported': [key for key in os.environ "
        "if key in {'WATCHDOG_START_VALIDATE_ONLY', 'WATCHDOG_VALIDATE_ONLY', "
        "'WATCHDOG_AUTO_VALIDATE_ONLY'}]}))\n",
        encoding="utf-8",
    )
    probe_makefile = tmp_path / "probe.mk"
    probe_makefile.write_text(
        f"include {ROOT / 'make' / '80-agent-enforcement.mk'}\n", encoding="utf-8",
    )
    environment = {
        key: text for key, text in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(probe_makefile), target, *([f"{variable}={value}"] if value is not None else []),
            f"UV={shlex.quote(sys.executable)} {shlex.quote(str(recorder))}",
            f"MAKE=make --no-print-directory -f {shlex.quote(str(probe_makefile))}",
        ],
        cwd=tmp_path, env=environment, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    policy = ("1" if operation == "--stop" else "0") if value is None else value
    if policy not in {"0", "1"}:
        policy = "invalid"
    owners = ["agent_watchdog.py", "task_watchdog.py"] if target == "watchdog-auto" else ["agent_watchdog.py"]
    payloads = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert payloads == [
        {"argv": ["run", "python3", f"scripts/{owner}", operation, "--validate-only", policy], "exported": []}
        for owner in owners
    ]
    assert not (tmp_path / "TRIPWIRE").exists()
    assert not (tmp_path / ".gate-logs").exists()
