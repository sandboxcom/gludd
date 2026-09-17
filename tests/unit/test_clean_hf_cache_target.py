"""Operator fallback contracts for application-owned model cache reclamation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import general_ludd.self_improve.model_lifecycle as lifecycle
from general_ludd.self_improve.hf_cache_delete import CacheDeletionError

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_target_body(name: str) -> str:
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    marker = f"\n{name}:"
    start = makefile.index(marker) + 1
    remainder = makefile[start:]
    next_target = remainder.find("\n\n")
    return remainder if next_target < 0 else remainder[:next_target]


def test_clean_hf_cache_delegates_to_the_lifecycle_manager() -> None:
    body = _make_target_body("clean-hf-cache")

    assert "python scripts/clean_hf_cache.py" in body
    assert "python -m general_ludd.self_improve.model_lifecycle" not in body
    assert "CLEAN_HF_CACHE_ROOT" in body
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert (
        "CLEAN_HF_CACHE_ROOT ?= $(GLUDD_SELF_IMPROVE_MODEL_CACHE)"
        in makefile
    )
    assert "CLEAN_HF_CACHE_REQUIRED_BYTES" in body
    assert "CLEAN_HF_CACHE_VALIDATE_ONLY" in body
    assert "rm -rf" not in body
    assert "bartowski" not in body
    assert "|| true" not in body


def test_clean_hf_cache_entrypoint_is_warning_strict_and_machine_readable(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "clean_hf_cache.py"),
            "--cache-root",
            str(tmp_path / "owned-cache"),
            "--required-bytes",
            "0",
            "--validate-only",
            "1",
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONWARNINGS": "error"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == "validated"


def test_clean_hf_cache_has_a_complete_make_target_contract() -> None:
    contract = json.loads(
        (_REPO_ROOT / "config" / "make_target_contract.json").read_text(
            encoding="utf-8"
        )
    )
    entries = {
        entry["name"]: entry
        for entry in contract["targets"]
    }

    entry = entries["clean-hf-cache"]
    assert entry["make_variables"] == [
        "CLEAN_HF_CACHE_ROOT",
        "CLEAN_HF_CACHE_REQUIRED_BYTES",
        "CLEAN_HF_CACHE_VALIDATE_ONLY",
    ]
    behavior = entry["behavior"]
    assert "CLEAN_HF_CACHE_ROOT=/tmp/gludd-clean-hf-cache-contract" in behavior
    assert "CLEAN_HF_CACHE_REQUIRED_BYTES=0" in behavior
    assert "CLEAN_HF_CACHE_VALIDATE_ONLY=1" in behavior


@pytest.mark.parametrize("validate_only", ["0", "1"])
def test_model_lifecycle_cache_cli_is_diagnostic_and_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    validate_only: str,
) -> None:
    cache_root = tmp_path / "owned-cache"

    assert (
        lifecycle.main(
            [
                "--cache-root",
                str(cache_root),
                "--required-bytes",
                "0",
                "--validate-only",
                validate_only,
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] in {"applied", "validated"}
    assert output["cache_key"]
    assert output["payload_bytes"] == 0
    assert output["required_bytes"] == 0
    assert output["removed_count"] == 0


def test_model_lifecycle_cache_cli_uses_runtime_cache_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configured = tmp_path / "runtime-cache"
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_CACHE", str(configured))

    assert (
        lifecycle.main(
            [
                "--cache-root",
                "",
                "--required-bytes",
                "0",
                "--validate-only",
                "1",
            ]
        )
        == 0
    )

    assert (configured / ".gludd" / "models").is_dir()
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


def test_model_lifecycle_cache_cli_refuses_unowned_pressure_without_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_root = tmp_path / "private-cache-token"
    unowned = cache_root / "models--external--model" / "blobs" / "partial.incomplete"
    unowned.parent.mkdir(parents=True)
    unowned.write_bytes(b"unowned")
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_QUOTA_BYTES", "1")
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_RESERVE_BYTES", "0")

    assert (
        lifecycle.main(
            [
                "--cache-root",
                str(cache_root),
                "--required-bytes",
                "0",
                "--validate-only",
                "1",
            ]
        )
        == 2
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "refused"
    assert output["can_reclaim"] is False
    assert output["eviction_candidate_count"] == 0
    assert "private-cache-token" not in repr(output)
    assert unowned.read_bytes() == b"unowned"


def test_model_lifecycle_cache_cli_returns_bounded_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        lifecycle.main(
            [
                "--cache-root",
                str(tmp_path / "cache"),
                "--required-bytes",
                "-1",
                "--validate-only",
                "1",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_type": "ValueError",
        "status": "refused",
    }


def test_model_lifecycle_cache_cli_exposes_only_allowlisted_deletion_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refused cleanup identifies its safe stage without leaking a cache path."""
    private_path = str(tmp_path / "private-cache-token")

    class RefusingManager:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def diagnose_reclaim(self, *, required_bytes: int) -> lifecycle.ModelCacheDiagnostic:
            return lifecycle.ModelCacheDiagnostic(
                cache_key="a" * 16,
                payload_bytes=2,
                required_bytes=required_bytes,
                quota_bytes=1,
                reserve_bytes=0,
                disk_free_bytes=10,
                owned_count=1,
                leased_count=0,
                eviction_candidate_count=1,
                under_pressure=True,
                can_reclaim=True,
            )

        def reclaim(self, *, required_bytes: int) -> tuple[Path, ...]:
            del required_bytes
            raise CacheDeletionError("cache deletion strategy does not target exact revision")

    monkeypatch.setattr(lifecycle, "ModelLeaseManager", RefusingManager)

    assert (
        lifecycle.main(
            [
                "--cache-root",
                private_path,
                "--required-bytes",
                "1",
                "--validate-only",
                "0",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "error_reason": "strategy_not_exact_revision",
        "error_type": "CacheDeletionError",
        "status": "refused",
    }
    assert "private-cache-token" not in captured.err
