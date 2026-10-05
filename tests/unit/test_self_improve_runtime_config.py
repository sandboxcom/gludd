"""Fail-closed tests for the managed self-improvement runtime config file."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from general_ludd.self_improve.runtime_config import load_self_improve_runtime_config
from general_ludd.self_improve.runtime_evidence_config import (
    configured_capability_evidence_store,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def test_empty_runtime_config_path_keeps_live_candidates_disabled() -> None:
    assert load_self_improve_runtime_config("") is None


def test_runtime_config_loads_one_owned_json_object(tmp_path: Path) -> None:
    config_path = tmp_path / "self-improve.json"
    payload = {
        "azure_containerapp": {"schema_version": 1, "enabled": False},
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_self_improve_runtime_config(str(config_path)) == payload


@pytest.mark.parametrize(
    "payload",
    ("[]", "null", '"text"', "{invalid"),
)
def test_runtime_config_rejects_non_object_or_invalid_json(
    tmp_path: Path,
    payload: str,
) -> None:
    config_path = tmp_path / "self-improve.json"
    config_path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="runtime configuration"):
        load_self_improve_runtime_config(str(config_path))


def test_runtime_config_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "self-improve.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        load_self_improve_runtime_config(str(link))


def test_runtime_config_rejects_oversized_file(tmp_path: Path) -> None:
    config_path = tmp_path / "self-improve.json"
    config_path.write_bytes(b" " * 65_537)

    with pytest.raises(ValueError, match="size limit"):
        load_self_improve_runtime_config(str(config_path))


def test_configured_evidence_store_accepts_only_an_absolute_regular_json_list(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text('[{"collection":"azure-proof"}]', encoding="utf-8")

    store = configured_capability_evidence_store(
        {"capability_evidence": {"schema_version": 1, "path": str(evidence_path)}}
    )

    assert isinstance(store, CapabilityEvidenceStore)
    assert configured_capability_evidence_store(None) is None
    assert configured_capability_evidence_store({}) is None


@pytest.mark.parametrize(
    "configuration",
    [
        cast(object, []),
        {"capability_evidence": []},
        {"capability_evidence": {"schema_version": 2, "path": "/tmp/evidence"}},
        {"capability_evidence": {"schema_version": 1, "path": ""}},
        {"capability_evidence": {"schema_version": 1, "path": "relative.json"}},
    ],
)
def test_configured_evidence_store_rejects_ambiguous_configuration(
    configuration: object,
) -> None:
    with pytest.raises(ValueError, match=r"self_improve|capability evidence"):
        configured_capability_evidence_store(
            cast(Mapping[str, object] | None, configuration)
        )


@pytest.mark.parametrize("payload", ["{invalid", "{}", "[1]"])
def test_configured_evidence_store_rejects_malformed_payloads(
    tmp_path: Path,
    payload: str,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="capability evidence"):
        configured_capability_evidence_store(
            {
                "capability_evidence": {
                    "schema_version": 1,
                    "path": str(evidence_path),
                }
            }
        )


def test_configured_evidence_store_rejects_symlink_and_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("[]", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    for path in (link, tmp_path):
        with pytest.raises(ValueError, match="capability evidence"):
            configured_capability_evidence_store(
                {"capability_evidence": {"schema_version": 1, "path": str(path)}}
            )
