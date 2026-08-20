"""Resource and registry contracts for the live game-development E2E harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import tests.e2e.test_game_dev_full_pipeline as pipeline_module
from tests.e2e._local_model_configs import require_model
from tests.e2e.test_game_dev_full_pipeline import (
    _LOCAL_MODEL_HOST,
    _REVIEWER_CFG,
    GameResult,
    _candidate_is_usable,
    _group_roles_by_artifact,
    _group_roles_by_runtime,
    _payload_limits,
    _role_configs_for_model,
    _start_grouped_servers,
)


def test_reviewer_uses_public_ci_safe_registry_artifact() -> None:
    assert _REVIEWER_CFG == {
        "name": "Qwen2.5-0.5B",
        "repo": "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        "filename": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        "context_size": 8192,
    }


def test_target_model_plans_and_codes_while_qwen_reviews() -> None:
    configs = _role_configs_for_model(require_model("Qwen2.5-0.5B-Instruct"))

    assert configs["planner"] == configs["coder"] == configs["reviewer"]
    assert list(_group_roles_by_artifact(configs).values()) == [
        ["planner", "coder", "reviewer"]
    ]


def test_live_servers_bind_unambiguous_ipv4_loopback() -> None:
    assert _LOCAL_MODEL_HOST == "127.0.0.1"


def test_profile_payload_limits_reserve_output_inside_context() -> None:
    assert _payload_limits(8192) == (7168, 1024)


def test_profile_payload_limits_reject_too_small_context() -> None:
    with pytest.raises(ValueError, match="context_size must exceed"):
        _payload_limits(1024)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (GameResult("snake", True, True, 80, 10), True),
        (GameResult("snake", True, True, 80, 10, "lifecycle failed"), "lifecycle failed"),
        (GameResult("snake", False, False, 0, 10, "syntax failed"), "syntax failed"),
    ],
)
def test_candidate_acceptance_requires_clean_deterministic_verification(
    monkeypatch: pytest.MonkeyPatch,
    result: GameResult,
    expected: bool | str,
) -> None:
    monkeypatch.setattr(pipeline_module, "_verify_code", lambda *_args: result)

    assert _candidate_is_usable("candidate", "snake", "/tmp/gludd-candidate-test") == expected


def test_candidate_verdict_emits_bounded_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = GameResult("snake", True, False, 80, 10, "missing interface\n" + "x" * 300)
    monkeypatch.setattr(pipeline_module, "_verify_code", lambda *_args: result)

    outcome = _candidate_is_usable("candidate", "snake", "/tmp/gludd-candidate-test")
    assert isinstance(outcome, str)
    assert outcome.startswith("missing interface")

    output = capsys.readouterr().out
    assert "reason=missing interface " in output
    assert len(output) < 280


def test_artifact_groups_share_one_download_for_duplicate_roles() -> None:
    role_configs = {
        "planner": {
            "repo": "org/planner",
            "filename": "planner.gguf",
            "context_size": 2048,
        },
        "coder": {
            "repo": "org/qwen",
            "filename": "qwen.gguf",
            "context_size": 32768,
        },
        "reviewer": {
            "repo": "org/qwen",
            "filename": "qwen.gguf",
            "context_size": 32768,
        },
    }

    assert _group_roles_by_artifact(role_configs) == {
        ("org/planner", "planner.gguf"): ["planner"],
        ("org/qwen", "qwen.gguf"): ["coder", "reviewer"],
    }


def test_runtime_groups_share_server_only_when_context_matches() -> None:
    role_configs = {
        "planner": {"context_size": 2048},
        "coder": {"context_size": 32768},
        "reviewer": {"context_size": 2048},
    }
    local_paths = {
        "planner": "/models/shared.gguf",
        "coder": "/models/shared.gguf",
        "reviewer": "/models/shared.gguf",
    }

    assert _group_roles_by_runtime(role_configs, local_paths) == {
        ("/models/shared.gguf", 2048): ["planner", "reviewer"],
        ("/models/shared.gguf", 32768): ["coder"],
    }


@dataclass
class _Server:
    server_id: str


class _Manager:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.created: list[Any] = []
        self.started: list[str] = []
        self.stop_calls = 0

    def create_server(self, config: Any) -> _Server:
        self.created.append(config)
        return _Server(server_id=f"server-{len(self.created)}")

    async def start_server(self, server_id: str) -> None:
        self.started.append(server_id)
        if self.fail_start:
            raise RuntimeError("startup failed")

    async def stop_all(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_grouped_server_start_reuses_runtime_and_binds_loopback() -> None:
    manager = _Manager()
    role_configs = {
        "planner": {"name": "small", "context_size": 2048},
        "coder": {"name": "qwen", "context_size": 4096},
        "reviewer": {"name": "qwen", "context_size": 4096},
    }
    paths = {
        "planner": "/models/small.gguf",
        "coder": "/models/qwen.gguf",
        "reviewer": "/models/qwen.gguf",
    }
    available_ports = iter((21001, 21002))

    ports = await _start_grouped_servers(manager, role_configs, paths, lambda: next(available_ports))

    assert ports == {"planner": 21001, "coder": 21002, "reviewer": 21002}
    assert len(manager.created) == 2
    assert all(config.host == "127.0.0.1" for config in manager.created)
    assert manager.stop_calls == 0


@pytest.mark.asyncio
async def test_grouped_server_start_rolls_back_partial_manager() -> None:
    manager = _Manager(fail_start=True)
    role_configs = {"planner": {"name": "small", "context_size": 2048}}
    paths = {"planner": "/models/small.gguf"}

    with pytest.raises(RuntimeError, match="Server start failed for small"):
        await _start_grouped_servers(manager, role_configs, paths, lambda: 21001)

    assert manager.stop_calls == 1
