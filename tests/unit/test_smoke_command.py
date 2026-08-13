from __future__ import annotations

import json
from pathlib import Path
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
    assert all(float(item["estimated_cost_usd"]) <= 10.0 for item in tests)


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
    args = parser.parse_args(["test", "smoke", "aws", "ec2-a100", "--json"])

    assert "test" in subcommand_map
    assert args.provider == "aws"
    assert args.test == "ec2-a100"
    assert args.json is True
    assert callable(args.func)


@pytest.mark.parametrize("test_name", ["gpu-a100_80", "gpu-a100-80"])
def test_cli_azure_a100_dynamic_smoke_name_normalization(
    test_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription-id")
    parser, _ = build_parser()
    args = parser.parse_args(["test", "smoke", "azure", test_name, "--json"])

    args.func(args)

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["provider"] == "azure"
    assert report["test"] == "gpu-a100-80"
    assert report["mode"] == "dry-run"


def test_unknown_smoke_test_exits_with_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown smoke test"):
        run_smoke("aws", "does-not-exist", env={})


def test_cli_has_no_fs_pkg_resources_runtime_warning_filter() -> None:
    import warnings

    import general_ludd

    assert general_ludd.__version__
    assert not any(
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


def test_smoke_reports_trace_sequence_and_declared_coverage_depth() -> None:
    report = run_smoke(
        "aws",
        "ec2-a100",
        env={"AWS_KEY": "AKIA_TEST_VALUE", "AWS_SECRET": "super-secret"},
    )

    assert report["trace_id"].startswith("trace-")
    assert report["coverage_depth"] == "preflight"
    assert report["functional_scope"] == ["configuration"]
    assert all(item["trace_id"] == report["trace_id"] for item in report["events"] + report["logs"])
    sequences = [item["sequence"] for item in report["trace"]]
    assert sequences == sorted(sequences)
    assert {item["type"] for item in report["trace"]} == {"event", "log"}


def test_registry_marks_model_ping_as_functional_not_full_when_live_is_required() -> None:
    tests = list_smoke_tests(provider="openrouter")
    by_name = {item["test"]: item for item in tests}

    assert by_name["credential-check"]["coverage_depth"] == "configuration"
    assert by_name["metadata"]["coverage_depth"] == "connectivity"
    assert by_name["model-ping"]["coverage_depth"] == "functional"
    assert "chat_request" in by_name["model-ping"]["functional_scope"]


def test_live_model_ping_posts_minimal_request_and_requires_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-looking-token")
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_http_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Any:
        calls.append((url, headers, payload, timeout))

        class Response:
            status_code = 200
            elapsed_ms = 11.0

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "OK"}}]}

        return Response()

    report = run_smoke(
        "openrouter",
        "model-ping",
        env={"OPENROUTER_API_KEY": "real-looking-token"},
        live=True,
        http_post=fake_http_post,
    )

    assert report["status"] == "pass"
    assert report["coverage_depth"] == "functional"
    assert report["metrics"]["http_requests"] == 1
    assert report["metrics"]["completion_seen"] == 1
    assert calls[0][0].endswith("/chat/completions")
    assert calls[0][2]["max_tokens"] == 1


def test_cli_smoke_json_writes_full_diagnostic_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AWS_KEY", "AKIA_TEST_VALUE")
    monkeypatch.setenv("AWS_SECRET", "super-secret")
    output_path = tmp_path / "aws-smoke.json"
    parser, _ = build_parser()
    args = parser.parse_args(
        ["test", "smoke", "aws", "ec2-a100", "--json", "--output", str(output_path)]
    )

    args.func(args)

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved == printed
    assert saved["trace_id"].startswith("trace-")
    assert saved["trace"]
    assert saved["analysis_prompt"].startswith("Analyze this Gludd smoke report")
    assert "Do not ask for secrets" in saved["analysis_prompt"]


def test_cli_smoke_default_cost_ceiling_is_ten_dollars() -> None:
    parser, _ = build_parser()
    args = parser.parse_args(["test", "smoke", "openai", "model-ping"])

    assert args.max_cost_usd == 10.0
    assert args.engine == "vllm"

    args = parser.parse_args(
        [
            "test",
            "smoke",
            "aws",
            "ec2-a100",
            "--provisioned",
            "--engine",
            "llamacpp",
        ]
    )
    assert args.engine == "llamacpp"


def fake_provisioned_http_get(url: str, timeout: float) -> Any:
    class Response:
        def __init__(self, status_code: int, payload: object | None = None, text: str = "") -> None:
            self.status_code = status_code
            self.elapsed_ms = 7.0
            self.text = text
            self._payload = payload

        def json(self) -> object:
            if self._payload is None:
                raise ValueError("no json body")
            return self._payload

    assert timeout == 2.0
    if url == "http://127.0.0.1:8000/health":
        return Response(200, {"status": "ok"}, "ok")
    if url == "http://127.0.0.1:8000/v1/models":
        return Response(200, {"data": [{"id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"}]})
    if url == "http://127.0.0.1:8000/metrics":
        return Response(
            200,
            None,
            "vllm:num_requests_running 0\nvllm:request_success_total 1\nprocess_cpu_seconds_total 1\n",
        )
    return Response(404, {"error": "not found"})


class FakeProvisioner:
    def __init__(
        self,
        *,
        endpoint_url: str | None = "http://127.0.0.1:8000/v1",
        destroy_error: Exception | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.destroy_error = destroy_error
        self.deployed_configs: list[Any] = []
        self.destroyed: list[str] = []

    async def deploy(self, config: Any) -> Any:
        self.deployed_configs.append(config)

        class Instance:
            instance_id = "smoke-instance-1"
            provider = config.provider
            gpu_type = config.gpu_type
            endpoint_url = self.endpoint_url

        return Instance()

    async def destroy(self, instance_id: str) -> None:
        self.destroyed.append(instance_id)
        if self.destroy_error is not None:
            raise self.destroy_error


def test_provisioned_smoke_deploys_runs_model_task_and_tears_down() -> None:
    provisioner = FakeProvisioner()
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_http_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Any:
        calls.append((url, headers, payload, timeout))

        class Response:
            status_code = 200
            elapsed_ms = 13.0

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "OK"}}]}

        return Response()

    report = run_smoke(
        "aws",
        "ec2-a100",
        env={"AWS_KEY": "AKIA_TEST_VALUE", "AWS_SECRET": "super-secret"},
        provisioned=True,
        region="us-east-1",
        max_cost_usd=10.0,
        provisioner=provisioner,
        http_post=fake_http_post,
        http_get=fake_provisioned_http_get,
    )

    assert report["status"] == "pass"
    assert report["mode"] == "provisioned"
    assert report["coverage_depth"] == "provisioned"
    assert report["metrics"]["completion_seen"] == 1
    assert provisioner.deployed_configs[0].gpu_type.value == "a100_80"
    assert provisioner.deployed_configs[0].max_cost_usd == 10.0
    assert provisioner.deployed_configs[0].engine.value == "vllm"
    assert provisioner.destroyed == ["smoke-instance-1"]
    assert calls[0][0] == "http://127.0.0.1:8000/v1/chat/completions"
    diagnostics = report["endpoint_diagnostics"]
    assert diagnostics["expected"]["engine"] == "vllm"
    assert diagnostics["expected"]["gpu_type"] == "a100_80"
    assert diagnostics["expected"]["max_cost_usd"] == 10.0
    assert "TinyLlama/TinyLlama-1.1B-Chat-v1.0" in diagnostics["models"]["model_ids"]
    assert "vllm:request_success_total" in diagnostics["metrics"]["metric_names"]
    assert report["metrics"]["engine_metrics_seen"] == 1
    assert report["metrics"]["process_metrics_seen"] == 1
    events = [event["name"] for event in report["events"]]
    assert "provision.request" in events
    assert "model.task.verified" in events
    assert "provision.teardown.complete" in events


def test_provisioned_smoke_teardown_failure_fails_report() -> None:
    provisioner = FakeProvisioner(destroy_error=RuntimeError("teardown failed"))

    def fake_http_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Any:
        class Response:
            status_code = 200

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "OK"}}]}

        return Response()

    report = run_smoke(
        "aws",
        "ec2-a100",
        env={"AWS_KEY": "AKIA_TEST_VALUE", "AWS_SECRET": "super-secret"},
        provisioned=True,
        max_cost_usd=10.0,
        provisioner=provisioner,
        http_post=fake_http_post,
        http_get=fake_provisioned_http_get,
    )

    assert report["status"] == "fail"
    assert provisioner.destroyed == ["smoke-instance-1"]
    assert any(event["name"] == "provision.teardown.failed" for event in report["events"])


def test_multi_provider_model_juggle_dry_run_plans_multiple_models() -> None:
    report = run_smoke("multi-provider", "model-juggle", env={})

    assert report["status"] == "pass"
    assert report["mode"] == "dry-run"
    assert report["coverage_depth"] == "orchestration"
    assert report["metrics"]["juggle_planned_legs"] >= 3
    assert report["metrics"]["juggle_unique_providers"] >= 3
    assert report["model_juggle"]["plan"]
    assert report["model_juggle"]["results"] == []
    assert any(event["name"] == "model.juggle.plan" for event in report["events"])


def test_smoke_registry_exposes_multi_model_juggle_surfaces() -> None:
    tests = {(item["provider"], item["test"]) for item in list_smoke_tests()}

    assert ("multi-provider", "model-juggle") in tests
    assert ("multi-platform", "model-juggle") in tests


def test_multi_provider_model_juggle_live_posts_configured_provider_legs() -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_http_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Any:
        calls.append((url, headers, payload, timeout))

        class Response:
            status_code = 200
            elapsed_ms = 9.0

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "OK"}}]}

        return Response()

    report = run_smoke(
        "multi-provider",
        "model-juggle",
        env={"OPENROUTER_API_KEY": "secret-openrouter", "GROQ_API_KEY": "secret-groq"},
        live=True,
        http_post=fake_http_post,
    )

    assert report["status"] == "pass"
    assert report["mode"] == "live"
    assert report["metrics"]["juggle_configured_legs"] == 2
    assert report["metrics"]["juggle_successful_legs"] == 2
    assert len(calls) == 2
    assert all(call[0].endswith("/chat/completions") for call in calls)
    assert all(call[1]["Authorization"].startswith("Bearer ") for call in calls)
    assert {result["status"] for result in report["model_juggle"]["results"]} == {"pass"}
    encoded = json.dumps(report)
    assert "secret-openrouter" not in encoded
    assert "secret-groq" not in encoded


def test_multi_platform_model_juggle_live_posts_configured_local_platforms() -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_http_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Any:
        calls.append((url, headers, payload, timeout))

        class Response:
            status_code = 200
            elapsed_ms = 6.0

            def json(self) -> dict[str, object]:
                return {"choices": [{"message": {"content": "OK"}}]}

        return Response()

    report = run_smoke(
        "multi-platform",
        "model-juggle",
        env={
            "VLLM_BASE_URL": "http://vllm.local:8000/v1",
            "LLAMACPP_BASE_URL": "http://llama.local:8080/v1",
            "GLUDD_SMOKE_LOCAL_MODEL": "local-test-model",
        },
        live=True,
        http_post=fake_http_post,
    )

    assert report["status"] == "pass"
    assert report["metrics"]["juggle_configured_legs"] == 2
    assert report["metrics"]["juggle_successful_legs"] == 2
    assert {call[0] for call in calls} == {
        "http://vllm.local:8000/v1/chat/completions",
        "http://llama.local:8080/v1/chat/completions",
    }
    assert {result["platform"] for result in report["model_juggle"]["results"]} == {"local-model"}
    assert {result["model"] for result in report["model_juggle"]["results"]} == {"local-test-model"}
