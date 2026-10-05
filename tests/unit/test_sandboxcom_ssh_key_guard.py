"""Guardrails for external sandboxcom SSH credential resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _target_block(target: str) -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i].startswith(("\t", " ", "#")) and ":" in lines[i]:
            end = i
            break
    return "\n".join(lines[start:end])


def test_ssh_key_defaults_outside_repository() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    assert "SSH_KEY ?= $(HOME)/.ssh/sandboxcom_gludd_rsa" in content
    assert "sandboxcom SSH key is missing or unreadable" in _target_block(
        "require-sandboxcom-ssh-key"
    )


def test_development_push_uses_external_key_guard() -> None:
    block = _target_block("development-push")
    assert "require-sandboxcom-ssh-key" in block
    assert "ssh -i $(SSH_KEY)" in block
    assert "/Users/shawnwilson/gludd/sandboxcom_gludd_rsa" not in block


def test_missing_key_fails_closed_with_setup_hint(tmp_path: Path) -> None:
    missing = tmp_path / "missing-key"
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "require-sandboxcom-ssh-key",
            f"SSH_KEY={missing}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "missing or unreadable" in output
    assert "Set SSH_KEY" in output
