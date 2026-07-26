"""Contract tests for local GPU smoke Make targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _target_block(name: str) -> str:
    lines = MAKEFILE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"{name}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(("\t", " ")):
            break
        body.append(line)
    return "\n".join(body)


def test_local_gpu_targets_exist_and_default_to_dry_run() -> None:
    for target in ("mac-unified-memory-smoke", "gpu-hardware-smoke"):
        block = _target_block(target)
        assert "$(UV) run python" in block
        assert "$(if $(filter 1 true yes,$(LIVE)),--live,)" in block
        assert "$(ARGS)" in block
        assert "$(BACKEND)" in block


def test_local_gpu_targets_forward_live_mode() -> None:
    for target in ("mac-unified-memory-smoke", "gpu-hardware-smoke"):
        block = _target_block(target)
        assert "$(LIVE)" in block
        assert "--live" in block


def test_gpu_hardware_make_target_is_credential_free_by_default() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "gpu-hardware-smoke", "BACKEND=auto"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"mode": "dry-run"' in result.stdout
