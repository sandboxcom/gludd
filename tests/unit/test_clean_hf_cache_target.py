"""Operator fallback contracts for application-owned model cache reclamation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import general_ludd.self_improve.model_lifecycle as lifecycle

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

    assert "python -m general_ludd.self_improve.model_lifecycle" in body
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
