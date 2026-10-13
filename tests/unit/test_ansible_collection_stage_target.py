"""Native Make transport for private, explicitly selected Galaxy artifact staging."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VARIABLES = [
    "ANSIBLE_COLLECTION_STAGE_MODE", "ANSIBLE_COLLECTION_STAGE_LOCK",
    "ANSIBLE_COLLECTION_STAGE_LOCK_SHA256", "ANSIBLE_COLLECTION_STAGE_REQUIREMENTS",
    "ANSIBLE_COLLECTION_STAGE_ARTIFACT_ROOT", "ANSIBLE_COLLECTION_STAGE_ORIGIN",
    "ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS", "ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY",
]


@pytest.mark.parametrize("mode", ["offline", "acquire", "refresh"])
def test_real_make_plan_keeps_literal_paths_and_never_acquires(
    tmp_path: Path, mode: str,
) -> None:
    requirements = tmp_path / 'required "pins".yml'
    requirements.write_text(json.dumps({"collections": [{"name": "fixture.main", "version": "1.0.0"}]}))
    generation = tmp_path / 'generation "quoted"; never-execute'
    lock = generation / "collections.lock.json"
    values = [mode, str(lock), "sha256:" + "a" * 64, str(requirements), str(generation),
              "https://galaxy.ansible.com/", "120", "1"]
    result = subprocess.run(
        ["make", "--no-print-directory", "stage-ansible-runtime-collections",
         *(f"{name}={value}" for name, value in zip(VARIABLES, values, strict=True))],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ANSIBLE_COLLECTION_STAGE_PLAN mode={mode}" in result.stdout
    assert str(generation) in result.stdout and str(lock) in result.stdout
    assert "timeout=120" in result.stdout and "proof=none" in result.stdout
    assert "ANSIBLE_COLLECTION_ARTIFACTS_READY" not in result.stdout
    assert not generation.exists()
    assert not lock.exists()


@pytest.mark.parametrize("variable,value,diagnostic", [
    ("ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY", "2", "must be 0 or 1"),
    ("ANSIBLE_COLLECTION_STAGE_MODE", "unknown", "invalid choice"),
    ("ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS", "0", "timeout must be an integer"),
    ("ANSIBLE_COLLECTION_STAGE_TIMEOUT_SECONDS", "121", "timeout must be an integer"),
    ("ANSIBLE_COLLECTION_STAGE_ORIGIN", "https://secret@example.invalid/", "credential-free HTTPS"),
])
def test_real_make_refuses_invalid_policy_without_acquisition(
    tmp_path: Path, variable: str, value: str, diagnostic: str,
) -> None:
    generation = tmp_path / "never-acquired"
    values = ["offline", str(generation / "collections.lock.json"), "", "config/ansible/requirements.yml",
              str(generation), "https://galaxy.ansible.com/", "120", "1"]
    selection = dict(zip(VARIABLES, values, strict=True))
    selection[variable] = value
    result = subprocess.run(
        ["make", "--no-print-directory", "stage-ansible-runtime-collections",
         *(f"{name}={selected}" for name, selected in selection.items())],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode != 0
    assert diagnostic in result.stdout + result.stderr
    assert "ANSIBLE_COLLECTION_STAGE_PLAN" not in result.stdout
    assert "ANSIBLE_COLLECTION_ARTIFACTS_READY" not in result.stdout
    assert not generation.exists()


def test_target_contract_documents_every_explicit_stage_input() -> None:
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    entries = [entry for entry in contract["targets"] if entry["name"] == "stage-ansible-runtime-collections"]
    assert len(entries) == 1
    assert entries[0]["make_variables"] == VARIABLES
    assert "ANSIBLE_COLLECTION_STAGE_VALIDATE_ONLY=1" in entries[0]["behavior"]
    assert "stage-ansible-runtime-collections" in (ROOT / "make/00-foundation.mk").read_text()
