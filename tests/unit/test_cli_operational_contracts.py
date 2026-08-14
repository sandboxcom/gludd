"""Substantive execution coverage for operator-facing CLI contracts."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd import cli


def _chat_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "daemon_url": None,
        "search": None,
        "list_sessions": False,
        "history": None,
        "resume": False,
        "save_interval": 5,
        "export": None,
        "export_output": None,
        "model": "test-model",
        "system_prompt": None,
        "eval": None,
        "api_base": None,
        "api_key": None,
        "project_dir": None,
        "max_context": None,
        "stream": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_login_lists_services_and_runs_selected_flow(capsys: pytest.CaptureFixture[str]) -> None:
    """Login discovery and credential-store selection stay executable."""
    preset = SimpleNamespace(display_name="GitHub", token_url="https://token.example")
    flow = MagicMock()
    flow.run.return_value = "token"
    with (
        patch("general_ludd.auth.browser_login.SERVICE_PRESETS", {"github": preset}),
        patch("general_ludd.auth.browser_login.list_services", return_value=["github"]),
    ):
        cli._cmd_login(Namespace(list=True, service=None, store="env", timeout=3.0))
    assert "GitHub" in capsys.readouterr().out

    with (
        patch("general_ludd.auth.browser_login.SERVICE_PRESETS", {"github": preset}),
        patch("general_ludd.auth.browser_login.EnvCredentialStore"),
        patch("general_ludd.auth.browser_login.BrowserLoginFlow", return_value=flow) as flow_factory,
    ):
        cli._cmd_login(Namespace(list=False, service="GitHub", store="env", timeout=3.0))
    flow_factory.assert_called_once()
    flow.run.assert_called_once_with(timeout=3.0)


def test_cloud_iam_and_generation_handlers_forward_complete_payloads(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cloud commands validate files and preserve every model-role argument."""
    with patch("general_ludd.cloud.core.generate_cloud_role", return_value={"status": "ok", "role": "reader"}):
        cli._cmd_cloud_iam_generate(Namespace(provider="aws", persona="reader"))

    role_file = tmp_path / "role.json"
    role_file.write_text(json.dumps({"Version": "2012-10-17"}), encoding="utf-8")
    with patch("general_ludd.cloud.core.validate_cloud_role", return_value={"status": "valid"}) as validate:
        cli._cmd_cloud_iam_validate(Namespace(provider="aws", file=str(role_file)))
    validate.assert_called_once_with("aws", {"Version": "2012-10-17"})

    common = Namespace(
        daemon_url="http://daemon",
        description="game",
        planner="planner",
        coder="coder",
        reviewer="reviewer",
        review_rounds=2,
    )
    create = Namespace(**vars(common), project_type="web")
    validate_args = Namespace(daemon_url="http://daemon", project_type="web", path="/project")
    with patch("general_ludd.cli._http_call", side_effect=[{}, {}, {}, {"valid": True}]) as call:
        for func, args in (
            (cli._cmd_cloud_game_generate_multi, common),
            (cli._cmd_cloud_generate_list_types, Namespace(daemon_url="http://daemon")),
            (cli._cmd_cloud_generate_create, create),
            (cli._cmd_cloud_generate_validate, validate_args),
        ):
            with pytest.raises(SystemExit) as exc_info:
                func(args)
            assert exc_info.value.code == 0
    assert call.call_count == 4
    assert "valid" in capsys.readouterr().out


def test_searx_lifecycle_commands_use_the_managed_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SearX lifecycle commands call install, stop, status, and config APIs."""
    server = MagicMock()
    server.ensure_started.return_value = True
    server.get_instance_url.return_value = "http://127.0.0.1:8080"
    with (
        patch("general_ludd.searx.install.ensure_searx_installed") as install,
        patch("general_ludd.searx.install.ensure_searx_initialized") as initialize,
        patch("general_ludd.searx.server.SearXServer", return_value=server),
    ):
        cli._cmd_searx(Namespace(searx_command="start"))
        cli._cmd_searx(Namespace(searx_command="stop"))
    install.assert_called_once()
    initialize.assert_called_once()
    server.stop.assert_called_once()

    settings = tmp_path / "settings.yml"
    settings.write_text("use_default_settings: true\n", encoding="utf-8")
    config = MagicMock()
    config.generate.return_value = settings
    with patch("general_ludd.searx.config.SearXConfig", return_value=config):
        cli._cmd_searx(Namespace(searx_command="config"))
    assert "use_default_settings" in capsys.readouterr().out


def test_terraform_get_and_set_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Terraform defaults can be inspected and atomically persisted by field."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        cli._cmd_config_terraform_get(Namespace(field=None))
        cli._cmd_config_terraform_set(Namespace(field="gpu_count", value="2"))
        cli._cmd_config_terraform_get(Namespace(field="gpu_count"))
    output = capsys.readouterr().out
    assert "gpu_count" in output
    assert "gpu_count = 2" in output
    assert (tmp_path / ".config/general-ludd/user.yml").is_file()


def test_chat_search_list_export_and_eval_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Chat inspection, export, and single-turn execution remain usable."""
    response = MagicMock()
    response.json.return_value = {
        "results": [{
            "timestamp": "now",
            "model": "m",
            "message_count": 2,
            "preview": "matched text",
            "file": "session.json",
            "match_source": "preview",
        }],
    }
    with patch("general_ludd.cli.httpx.post", return_value=response):
        cli._cmd_chat(_chat_args(daemon_url="http://daemon", search="matched"))
    response.raise_for_status.assert_called_once()

    class FakeChatSession:
        @staticmethod
        def list_sessions() -> list[dict[str, object]]:
            return [{"timestamp": "now", "model": "m", "message_count": 1, "preview": "hello", "file": "s"}]

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run_once(self, prompt: str) -> str:
            assert prompt == "question"
            return "answer"

    with patch("general_ludd.chat.ChatSession", FakeChatSession):
        cli._cmd_chat(_chat_args(list_sessions=True))
        cli._cmd_chat(_chat_args(eval="question"))

    history = tmp_path / "session.json"
    history.write_text("{}", encoding="utf-8")
    with (
        patch("general_ludd.chat.ChatSession", FakeChatSession),
        patch("general_ludd.chat.session.export_session", return_value="exported") as export,
    ):
        cli._cmd_chat(_chat_args(history=str(history), export="json"))
    export.assert_called_once()
    output = capsys.readouterr().out
    assert "matched text" in output
    assert "answer" in output
    assert "exported" in output


def test_model_performance_ranking_and_router_outputs(capsys: pytest.CaptureFixture[str]) -> None:
    """Model metrics and router commands render canonical backend responses."""
    row = {
        "service": "svc",
        "model_name": "model",
        "task_type": "chat",
        "success_rate": 0.9,
        "avg_latency_ms": 12,
        "avg_cost_usd": 0.001,
        "sample_count": 4,
        "score": 0.8,
    }
    responses = [
        {"performance": [row]},
        {"task_type": "chat", "strategy": "quality", "ranking": [row]},
        {
            "status": "ready",
            "config": {
                "strategies": {"chat": "quality"},
                "defaults": {"min_calls": 3, "default_fallback": "m"},
            },
        },
        {"task_type": "chat", "strategy": "quality"},
    ]
    with patch("general_ludd.cli._http_call", side_effect=responses):
        cli._cmd_model_performance(Namespace(daemon_url="http://d", service="svc", task_type="chat"))
        cli._cmd_model_ranking(Namespace(daemon_url="http://d", task_type="chat", strategy="quality"))
        cli._cmd_model_router_status(Namespace(daemon_url="http://d"))
        cli._cmd_model_router_set(Namespace(daemon_url="http://d", task_type="chat", strategy="quality"))
    output = capsys.readouterr().out
    assert "svc" in output
    assert "Per-task strategies" in output
    assert "Strategy set" in output


def test_preflight_renders_issues_and_violations(capsys: pytest.CaptureFixture[str]) -> None:
    """Preflight output keeps importer issues and bounded violation details."""
    result = {
        "overall": "PASS",
        "passed_count": 1,
        "total_count": 2,
        "checks": [
            {
                "name": "terraform_collection_import_audit",
                "passed": True,
                "issues": [{"severity": "warn", "message": "legacy import"}],
            },
            {"name": "lint", "passed": False, "violations": ["one", "two"]},
        ],
    }
    with patch("general_ludd.quality.preflight.run_preflight", return_value=result):
        cli._cmd_preflight(Namespace(strict_terraform_import=True))
    output = capsys.readouterr().out
    assert "legacy import" in output
    assert "one" in output


def test_slurm_handlers_forward_optional_resources_and_render_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Slurm commands preserve resource options and lifecycle identifiers."""
    responses = [
        {"available": True},
        {"job_id": "42"},
        {"job_id": "42", "state": "DONE", "exit_code": 0},
        {},
        {"jobs": [{"job_id": "42", "state": "DONE", "exit_code": 0}]},
        {"jobs": []},
    ]
    submit = Namespace(
        daemon_url="http://d",
        command="hostname",
        job_name="job",
        partition="gpu",
        cpus_per_task=4,
        gpus="a100:1",
        memory="16G",
        time_limit="00:10:00",
    )
    with patch("general_ludd.cli._http_call", side_effect=responses) as call:
        cli._cmd_slurm_status(Namespace(daemon_url="http://d"))
        cli._cmd_slurm_submit(submit)
        cli._cmd_slurm_job(Namespace(daemon_url="http://d", job_id="42"))
        cli._cmd_slurm_cancel(Namespace(daemon_url="http://d", job_id="42"))
        cli._cmd_slurm_list(Namespace(daemon_url="http://d"))
        cli._cmd_slurm_list(Namespace(daemon_url="http://d"))
    payload = call.call_args_list[1].kwargs["json"]
    assert payload["gpus"] == "a100:1"
    output = capsys.readouterr().out
    assert "Submitted job: 42" in output
    assert "No Slurm jobs" in output


def test_slurm_minimal_submission_omits_unrequested_resources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A minimal Slurm request never invents resource constraints."""
    submit = Namespace(
        daemon_url="http://d",
        command="hostname",
        job_name=None,
        partition=None,
        cpus_per_task=None,
        gpus=None,
        memory=None,
        time_limit=None,
    )
    with patch("general_ludd.cli._http_call", return_value=None) as call:
        cli._cmd_slurm_submit(submit)

    assert call.call_args.kwargs["json"] == {"command": "hostname"}
    assert "Submitted job" not in capsys.readouterr().out


def test_make_handler_streams_phases_and_propagates_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """Make command reports streamed phases and returns the runner status."""
    success = SimpleNamespace(
        target="lint",
        exit_code=0,
        success=True,
        duration_s=0.1,
        timed_out=False,
        phases=["lint"],
    )
    runner = MagicMock()

    def run_success(*_args: object, **kwargs: object) -> object:
        callback = kwargs.get("stream_callback")
        assert callable(callback)
        callback("lint")
        return success

    runner.run.side_effect = run_success
    args = Namespace(env=["TOKEN=value", "ignored"], cwd=None, stream=True, target="lint", timeout=10.0)
    with patch("general_ludd.commands.make.MakeRunner", return_value=runner), pytest.raises(SystemExit) as exc_info:
        cli._cmd_make(args)
    assert exc_info.value.code == 0

    failure = SimpleNamespace(
        target="test",
        exit_code=2,
        success=False,
        duration_s=0.2,
        timed_out=False,
        phases=[],
    )
    runner.run.side_effect = None
    runner.run.return_value = failure
    args = Namespace(env=None, cwd=None, stream=False, target="test", timeout=10.0)
    with patch("general_ludd.commands.make.MakeRunner", return_value=runner), pytest.raises(SystemExit) as exc_info:
        cli._cmd_make(args)
    assert exc_info.value.code == 1
    assert "[PHASE] lint" in capsys.readouterr().out


def test_quantization_handlers_fail_closed_on_malformed_collections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed collection members are ignored without corrupting output."""
    with patch("general_ludd.cli._http_call", return_value={"models": "invalid"}):
        cli._cmd_quantization_list(Namespace(daemon_url="http://d"))
    assert "No quantization data" in capsys.readouterr().out

    with patch(
        "general_ludd.cli._http_call",
        return_value={
            "drift_detected": True,
            "changes": ["invalid", {"model_id": "m", "old_precision": "fp16", "new_precision": "int8"}],
        },
    ):
        cli._cmd_quantization_drift_check(Namespace(daemon_url="http://d"))
    output = capsys.readouterr().out
    assert "m: fp16 -> int8" in output
    assert "invalid:" not in output


def test_quantization_handlers_return_silently_when_daemon_has_no_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing daemon payloads do not fabricate quantization recommendations."""
    args = Namespace(daemon_url="http://d", model_id="model")
    with patch("general_ludd.cli._http_call", return_value=None):
        cli._cmd_quantization_list(args)
        cli._cmd_quantization_detect(args)
        cli._cmd_quantization_drift_check(args)

    assert capsys.readouterr().out == ""
