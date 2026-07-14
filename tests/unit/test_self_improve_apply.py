"""Structural tests for SelfApply and ExternalApply."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from general_ludd.self_improve.apply import ExternalApply, SelfApply


class _FakeGitAutomation:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path

    def changed_files(self) -> list[str]:
        return ["src/a.py", "src/b.py"]

    def commit_and_push(self, message: str) -> str:
        return "abc123def"


class _FakeReloader:
    """Returns a result object whose .details is a dict."""
    def __init__(self, success: bool = True, modules: list[str] | None = None) -> None:
        self._success = success
        self._modules = modules or ["src.a", "src.b"]

    def reload_changed_modules(
        self,
        repo_dir: str,
        changed_paths: list[str],
        health_check: Any = None,
        role: str | None = None,
    ) -> Any:
        reloader = self

        class _R:
            success = reloader._success

            @property
            def details(self) -> dict[str, Any]:
                return {"reloaded_modules": list(reloader._modules), "added_modules": []}

        return _R()


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


class TestSelfApply:
    def test_apply_returns_commit_sha(self) -> None:
        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = SelfApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: test",
                reloader=_FakeReloader(),
            )
            assert result["commit_sha"] == "abc123def"
            assert result["changed_files"] == ["src/a.py", "src/b.py"]
            assert result["reload_success"] is True
            assert "src.a" in result["reloaded_modules"]

    def test_apply_publishes_event(self) -> None:
        bus = _FakeEventBus()
        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = SelfApply()
            applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: test",
                reloader=_FakeReloader(),
                event_bus=bus,
            )
            assert len(bus.events) == 1
            evt = bus.events[0]
            assert evt.payload["commit_sha"] == "abc123def"
            assert "src.a" in evt.payload["reloaded_modules"]

    def test_apply_no_event_bus_no_publish(self) -> None:
        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = SelfApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: test",
                reloader=_FakeReloader(),
                event_bus=None,
            )
            assert result["commit_sha"] == "abc123def"

    def test_apply_reloader_dict_result(self) -> None:
        class _DictReloader:
            def reload_changed_modules(
                self, repo_dir: str, changed_paths: list[str],
                health_check: Any = None, role: str | None = None,
            ) -> dict[str, Any]:
                return {"success": True, "reloaded_modules": ["mod.x"], "added_modules": []}

        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = SelfApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: test",
                reloader=_DictReloader(),
            )
            assert "mod.x" in result["reloaded_modules"]

    def test_apply_reloader_no_details(self) -> None:
        class _NoDetailsReloader:
            def reload_changed_modules(
                self, repo_dir: str, changed_paths: list[str],
                health_check: Any = None, role: str | None = None,
            ) -> Any:
                class _R:
                    success = False
                return _R()

        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = SelfApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: test",
                reloader=_NoDetailsReloader(),
            )
            assert result["reload_success"] is False
            assert result["reloaded_modules"] == []


class TestExternalApply:
    def test_apply_returns_result(self) -> None:
        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = ExternalApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: external change",
            )
            assert result["commit_sha"] == "abc123def"
            assert result["changed_files"] == ["src/a.py", "src/b.py"]
            assert "reloaded_modules" not in result
            assert "reload_success" not in result

    def test_apply_no_event_bus(self) -> None:
        with patch(
            "general_ludd.git_automation.repo.GitAutomation",
            return_value=_FakeGitAutomation("/tmp/repo"),
        ):
            applier = ExternalApply()
            result = applier.apply(
                workspace_repo_dir="/tmp/repo",
                message="fix: external change",
                event_bus=None,
            )
            assert result["commit_sha"] == "abc123def"
