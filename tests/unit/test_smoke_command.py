from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.cli import build_parser
from general_ludd.infra.compute import ComputeProvider
from general_ludd.models.provider_presets import PROVIDER_PRESETS
from general_ludd.smoke import list_smoke_tests, run_smoke


def test_smoke_registry_covers_model_and_compute_providers() -> None:
    providers = {item["provider"] for item in list_smoke_tests()}

    assert set(PROVIDER_PRESETS).issubset(providers)
    assert {provider.value for provider in ComputeProvider}.issubset(providers)
    assert {"ollama", "vllm", "llamacpp", "slurm"}.issubset(providers)


def test_smoke_list_exposes_low_cost_defaults() -> None:
    tests = list_smoke_tests(provider="aws")
    names = {item["test"] for item in tests}

    assert "credential-check" in names
    assert "ec2-a100" in names
    assert all(item["default_live"] is False for item in tests)
    assert all(float(item["estimated_cost_usd"]) <= 0.01 for item in tests)


def test_aws_ec2_a100_accepts_user_friendly_env_aliases_and_redacts_values() -> None:
    report = run_smoke(
        "aws",
        "ec2-a100",
        env={
            "AWS_KEY": "AKIA_TEST_VALUE",  # pragma: allowlist secret
            "AWS_SECRET": "example-value",  # pragma: allowlist secret
            "AWS_DEFAULT_REGION": "us-east-1",
        },
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["status"] == "pass"
    assert report["provider"] == "aws"
    assert report["test"] == "ec2-a100"
    assert report["mode"] == "dry-run"
    assert "AKIA_TEST_VALUE" not in encoded
    assert "super-secret" not in encoded
    assert report["metrics"]["checks_failed"] == 0
    assert any(event["name"] == "credentials.validated" for event in report["events"])


def test_missing_credentials_fails_without_network_or_cost() -> None:
    report = run_smoke("openai", "model-ping", env={}, live=False)

    assert report["status"] == "fail"
    assert report["mode"] == "dry-run"
    assert report["estimated_cost_usd"] == 0.0
    assert report["metrics"]["checks_failed"] >= 1
    assert any(event["name"] == "credentials.missing" for event in report["events"])


def test_live_metadata_probe_records_http_metrics_without_payload_dump() -> None:
    def fake_http_get(url: str, timeout: float) -> Any:
        class Response:
            status_code = 200
            elapsed_ms = 17.0

            def json(self) -> dict[str, object]:
                return {"data": [{"id": "model-a"}, {"id": "model-b"}]}

        assert url == "https://openrouter.ai/api/v1/models"
        assert timeout == 2.0
        return Response()

    report = run_smoke(
        "openrouter",
        "metadata",
        env={"OPENROUTER_API_KEY": "example-token"},  # pragma: allowlist secret
        live=True,
        http_get=fake_http_get,
    )

    assert report["status"] == "pass"
    assert report["mode"] == "live"
    assert report["metrics"]["http_requests"] == 1
    assert report["metrics"]["models_seen"] == 2
    assert report["events"][-1]["name"] == "smoke.completed"
    assert "secret-token" not in json.dumps(report)


def test_cli_smoke_subcommand_parses_provider_and_test() -> None:
    parser, subcommand_map = build_parser()
    args = parser.parse_args(["smoke", "aws", "ec2-a100", "--json"])

    assert "smoke" in subcommand_map
    assert args.provider == "aws"
    assert args.test == "ec2-a100"
    assert args.json is True
    assert callable(args.func)


def test_unknown_smoke_test_exits_with_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown smoke test"):
        run_smoke("aws", "does-not-exist", env={})


def test_cli_filters_fs_pkg_resources_runtime_warning() -> None:
    import warnings

    import general_ludd

    assert general_ludd.__version__
    assert any(
        getattr(item[1], "pattern", "").startswith("pkg_resources is deprecated")
        and getattr(item[3], "pattern", "") == "fs"
        for item in warnings.filters
    )


def test_live_metadata_auth_rejection_is_not_provider_health_pass() -> None:
    def fake_http_get(url: str, timeout: float) -> Any:
        class Response:
            status_code = 401
            elapsed_ms = 5.0

            def json(self) -> dict[str, object]:
                return {"error": {"message": "invalid api key"}}

        return Response()

    report = run_smoke(
        "openrouter",
        "metadata",
        env={"OPENROUTER_API_KEY": "fake-token"},  # pragma: allowlist secret
        live=True,
        http_get=fake_http_get,
    )

    assert report["status"] == "auth_rejected"
    assert report["metrics"]["auth_rejected"] == 1
    assert report["metrics"]["checks_failed"] >= 1
    assert any(event["name"] == "auth.rejected" for event in report["events"])
