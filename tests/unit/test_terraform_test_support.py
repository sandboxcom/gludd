"""Tests for the shared Terraform external-dependency test boundary."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest

from tests import terraform_test_support
from tests.terraform_test_support import (
    is_known_external_terraform_provider_failure,
    skip_external_terraform_dependency,
)


@pytest.mark.parametrize("inherited", [None, "", "owned"])
def test_provider_environment_preserves_shard_cache_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: str | None,
) -> None:
    fallback = tmp_path / "checkout-cache"
    owned = tmp_path / "owned-cache"
    if inherited is None:
        monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
    else:
        monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(owned) if inherited else "")
    before = dict(os.environ)

    environment = terraform_test_support.terraform_test_environment(fallback)

    selected = owned if inherited else fallback
    assert environment["TF_PLUGIN_CACHE_DIR"] == str(selected.resolve())
    assert selected.is_dir()
    assert not (fallback if inherited else owned).exists()
    assert environment is not os.environ
    assert dict(os.environ) == before
    assert {k: v for k, v in environment.items() if k != "TF_PLUGIN_CACHE_DIR"} == {
        k: v for k, v in before.items() if k != "TF_PLUGIN_CACHE_DIR"
    }


def test_provider_environment_refuses_invalid_selected_cache_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "not-a-directory"
    selected.write_text("owned sentinel", encoding="utf-8")
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(selected))

    with pytest.raises(FileExistsError):
        terraform_test_support.terraform_test_environment(fallback)

    assert selected.read_text(encoding="utf-8") == "owned sentinel"
    assert not fallback.exists()


@pytest.mark.parametrize("error", [PermissionError("cache is read-only"), OSError("cache is full")])
def test_provider_environment_propagates_selected_cache_io_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError,
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(selected))
    before = dict(os.environ)
    probes: list[object] = []

    def refuse_probe(*args: object, **kwargs: object) -> NoReturn:
        probes.append(kwargs.get("dir"))
        raise error

    monkeypatch.setattr(tempfile, "TemporaryFile", refuse_probe)
    with pytest.raises(OSError) as captured:
        terraform_test_support.terraform_test_environment(fallback)

    assert captured.value is error
    assert probes == [selected.resolve()]
    assert not fallback.exists()
    assert list(selected.iterdir()) == []
    assert dict(os.environ) == before


def test_provider_environment_checks_real_directory_write_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(selected))
    selected.chmod(0o500)
    try:
        if os.access(selected, os.W_OK):
            # Privileged runners can write despite mode bits; test actual access.
            environment = terraform_test_support.terraform_test_environment(fallback)
            assert environment["TF_PLUGIN_CACHE_DIR"] == str(selected.resolve())
        else:
            with pytest.raises(PermissionError):
                terraform_test_support.terraform_test_environment(fallback)
    finally:
        selected.chmod(0o700)

    assert not fallback.exists()
    assert list(selected.iterdir()) == []


def test_external_dependency_skip_preserves_a_bounded_reason() -> None:
    reason = "provider registry unavailable: " + ("detail " * 200)

    with pytest.raises(pytest.skip.Exception) as exc_info:
        skip_external_terraform_dependency(reason)

    rendered = str(exc_info.value)
    assert rendered.startswith("provider registry unavailable:")
    assert len(rendered) <= 512


def test_external_dependency_skip_rejects_an_empty_reason() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        skip_external_terraform_dependency("   ")


@pytest.mark.parametrize(
    ("data_source", "attribute"),
    [
        ("runpod_endpoint_jobs", "jobs"),
        ("runpod_endpoint_workers", "workers"),
    ],
)
def test_runpod_schema_conversion_failure_is_external(
    data_source: str,
    attribute: str,
) -> None:
    diagnostic = f"""
    Failed to obtain provider schema: Could not load the schema for provider
    registry.terraform.io/runpod/runpod: Error converting data source schema:
    The schema for the data source "{data_source}" couldn't be converted into a
    usable type. AttributeName("{attribute}"): must have Required, Optional,
    or Computed set.
    """

    assert is_known_external_terraform_provider_failure(
        provider_family="runpod",
        stdout=json.dumps({"diagnostics": [{"detail": diagnostic}]}),
        stderr="",
    )


def test_azure_provider_registry_timeout_is_external() -> None:
    diagnostic = """
    Error: Failed to query available provider packages
    Could not retrieve the list of available versions for provider azure/azapi:
    could not connect to registry.terraform.io: failed to request discovery
    document: GET https://registry.terraform.io/.well-known/terraform.json
    giving up after 4 attempt(s): context deadline exceeded
    """

    assert is_known_external_terraform_provider_failure(
        provider_family="azure",
        stdout=diagnostic,
        stderr="",
    )


@pytest.mark.parametrize(
    ("provider_family", "diagnostic"),
    [
        ("aws", "runpod_endpoint_jobs AttributeName(\"jobs\")"),
        ("runpod", "Failed to obtain provider schema"),
        (
            "runpod",
            "registry.terraform.io/runpod/runpod runpod_endpoint_jobs "
            "AttributeName(\"other\"): must have Required, Optional, or "
            "Computed set.",
        ),
        (
            "azure",
            "Failed to query available provider packages for azure/azapi: "
            "the selected version does not match the configuration",
        ),
        (
            "azure",
            "could not connect to registry.terraform.io for hashicorp/aws: "
            "context deadline exceeded",
        ),
    ],
)
def test_unrelated_or_incomplete_provider_failure_is_not_external(
    provider_family: str,
    diagnostic: str,
) -> None:
    assert not is_known_external_terraform_provider_failure(
        provider_family=provider_family,
        stdout=diagnostic,
        stderr="",
    )
