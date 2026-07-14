"""Tests for D.3: ExternalApply vs SelfApply split (self_improve/apply.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.self_improve.apply import ExternalApply, SelfApply


class TestSelfApply:
    def test_apply_calls_git_and_reloader(self):
        sa = SelfApply()
        mock_reloader = MagicMock()
        mock_reloader.reload_changed_modules.return_value = MagicMock(
            success=True, details={"reloaded_modules": ["mod.py"], "added_modules": []}
        )

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = ["src/x.py", "tests/test_x.py"]
            mock_git.commit_and_push.return_value = "abc123def"
            mock_git_cls.return_value = mock_git

            result = sa.apply(
                workspace_repo_dir="/tmp/ws",
                message="fix: add retry",
                reloader=mock_reloader,
            )

        assert result["commit_sha"] == "abc123def"
        assert "src/x.py" in result["changed_files"]
        assert result["reload_success"] is True
        assert "mod.py" in result["reloaded_modules"]

    def test_apply_publishes_event_when_event_bus_present(self):
        sa = SelfApply()
        mock_reloader = MagicMock()
        mock_reloader.reload_changed_modules.return_value = MagicMock(
            success=True, details={"reloaded_modules": [], "added_modules": []}
        )
        mock_bus = MagicMock()

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = ["src/x.py"]
            mock_git.commit_and_push.return_value = "def456"
            mock_git_cls.return_value = mock_git

            sa.apply(
                workspace_repo_dir="/tmp/ws",
                message="fix: retry",
                reloader=mock_reloader,
                event_bus=mock_bus,
            )

        mock_bus.publish.assert_called_once()
        published = mock_bus.publish.call_args[0][0]
        assert published.payload["commit_sha"] == "def456"

    def test_apply_no_event_bus_no_publish(self):
        sa = SelfApply()
        mock_reloader = MagicMock()
        mock_reloader.reload_changed_modules.return_value = MagicMock(
            success=True, details={"reloaded_modules": [], "added_modules": []}
        )

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = []
            mock_git.commit_and_push.return_value = "ghi789"
            mock_git_cls.return_value = mock_git

            result = sa.apply(
                workspace_repo_dir="/tmp/ws",
                message="fix",
                reloader=mock_reloader,
            )

        assert result["commit_sha"] == "ghi789"

    def test_reloader_dict_result_shape(self):
        sa = SelfApply()
        mock_reloader = MagicMock()
        mock_reloader.reload_changed_modules.return_value = {
            "success": True,
            "reloaded_modules": ["a.py"],
            "added_modules": ["b.py"],
        }

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = ["a.py"]
            mock_git.commit_and_push.return_value = "sha"
            mock_git_cls.return_value = mock_git

            result = sa.apply(
                workspace_repo_dir="/tmp/ws",
                message="fix",
                reloader=mock_reloader,
            )

        assert "a.py" in result["reloaded_modules"]
        assert "b.py" in result["reloaded_modules"]

    def test_reloader_plain_object_shape(self):
        sa = SelfApply()
        mock_reloader = MagicMock()
        mock_result = MagicMock()
        del mock_result.details
        mock_result.success = True
        mock_reloader.reload_changed_modules.return_value = mock_result

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = []
            mock_git.commit_and_push.return_value = "sha"
            mock_git_cls.return_value = mock_git

            result = sa.apply(
                workspace_repo_dir="/tmp/ws",
                message="fix",
                reloader=mock_reloader,
            )

        assert result["reloaded_modules"] == []


class TestExternalApply:
    def test_apply_commits_no_reload(self):
        ea = ExternalApply()

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = ["ext/some_file.py"]
            mock_git.commit_and_push.return_value = "abc000def"
            mock_git_cls.return_value = mock_git

            result = ea.apply(
                workspace_repo_dir="/tmp/ext-ws",
                message="fix: external project gap",
            )

        assert "commit_sha" in result
        assert result["commit_sha"] == "abc000def"
        assert "ext/some_file.py" in result["changed_files"]
        assert "reload_success" not in result
        assert "reloaded_modules" not in result

    def test_apply_no_reload_keys_in_result(self):
        ea = ExternalApply()

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = []
            mock_git.commit_and_push.return_value = "sha111"
            mock_git_cls.return_value = mock_git

            result = ea.apply(workspace_repo_dir="/tmp/x", message="fix")

        assert set(result.keys()) == {"commit_sha", "changed_files"}
        assert result["commit_sha"] == "sha111"

    def test_external_apply_never_publishes_event(self):
        ea = ExternalApply()
        mock_bus = MagicMock()

        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = ["x"]
            mock_git.commit_and_push.return_value = "sha"
            mock_git_cls.return_value = mock_git

            ea.apply(
                workspace_repo_dir="/tmp/y",
                message="ignore",
                event_bus=mock_bus,
            )

        mock_bus.publish.assert_not_called()

    def test_accepts_event_bus_but_ignores(self):
        ea = ExternalApply()
        with patch(
            "general_ludd.git_automation.repo.GitAutomation"
        ) as mock_git_cls:
            mock_git = MagicMock()
            mock_git.changed_files.return_value = []
            mock_git.commit_and_push.return_value = "s"
            mock_git_cls.return_value = mock_git
            result = ea.apply("/tmp/z", "msg")
        assert result["commit_sha"] == "s"


class TestModuleExports:
    def test_apply_module_in_package_public_api(self):
        from general_ludd.self_improve import ExternalApply, SelfApply

        assert ExternalApply is not None
        assert SelfApply is not None

    def test_harness_still_exports_apply_self_improvement(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        assert hasattr(SelfImprovementHarness, "apply_self_improvement")
