"""Tests for ModelGateway.route_for_task — task-specific model routing."""

from __future__ import annotations

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile


def _profile(pid: str, model_name: str = "", enabled: bool = True) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        enabled=enabled,
        provider="openai",
        model_name=model_name or pid,
        api_metered=False,
    )


class TestRouteForTask:
    def test_code_routes_to_deepseek_coder(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-coder-v3", "deepseek-coder-v3"),
                _profile("qwen2.5", "qwen2.5"),
            ]
        )
        result = gw.route_for_task("code")
        assert result == "deepseek-coder-v3"

    def test_code_routes_to_glm4_when_no_deepseek(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("qwen2.5", "qwen2.5"),
                _profile("glm-4-flash", "glm-4-flash"),
            ]
        )
        result = gw.route_for_task("code")
        assert result == "glm-4-flash"

    def test_ansible_routes_to_qwen_coder(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-v3", "deepseek-v3"),
                _profile("qwen2.5-coder-7b", "qwen2.5-coder-7b-instruct"),
            ]
        )
        result = gw.route_for_task("ansible")
        assert result == "qwen2.5-coder-7b"

    def test_general_routes_to_default(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("default", "default-model"),
                _profile("qwen2.5", "qwen2.5"),
            ]
        )
        result = gw.route_for_task("general")
        assert result == "default"

    def test_general_falls_back_when_no_default(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-v3", "deepseek-v3"),
                _profile("qwen2.5", "qwen2.5"),
            ]
        )
        result = gw.route_for_task("general")
        assert result == "deepseek-v3"

    def test_game_routes_to_qwen(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-v3", "deepseek-v3"),
                _profile("qwen2.5", "qwen2.5-72b"),
            ]
        )
        result = gw.route_for_task("game")
        assert result == "qwen2.5"

    def test_falls_back_to_any_enabled(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("unknown-model", "unknown-model"),
            ]
        )
        result = gw.route_for_task("code")
        assert result == "unknown-model"

    def test_raises_when_no_enabled_profiles(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("disabled-1", "model-1", enabled=False),
            ]
        )
        with pytest.raises(ValueError, match="No enabled profile"):
            gw.route_for_task("code")

    def test_unknown_task_kind_uses_default_preferences(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-v3", "deepseek-v3"),
                _profile("other", "other-model"),
            ]
        )
        result = gw.route_for_task("unknown-task-type")
        assert result == "deepseek-v3"

    def test_case_insensitive(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-coder-v3", "deepseek-coder-v3"),
            ]
        )
        result = gw.route_for_task("CODE")
        assert result == "deepseek-coder-v3"

    def test_skips_disabled_profiles(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-coder", "deepseek-coder", enabled=False),
                _profile("qwen2.5-coder-7b", "qwen2.5-coder-7b"),
            ]
        )
        result = gw.route_for_task("code")
        assert result == "qwen2.5-coder-7b"

    def test_matches_by_model_name(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("custom-profile-1", "Qwen/Qwen2.5-Coder-7B-Instruct"),
            ]
        )
        result = gw.route_for_task("code")
        assert result == "custom-profile-1"

    def test_game_prefers_claude_or_qwen(self) -> None:
        gw = ModelGateway(
            profiles=[
                _profile("deepseek-v3", "deepseek-v3"),
                _profile("claude-3.5-sonnet", "claude-3.5-sonnet"),
            ]
        )
        result = gw.route_for_task("game")
        assert result == "claude-3.5-sonnet"
