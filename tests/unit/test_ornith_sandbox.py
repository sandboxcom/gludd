"""TDD tests for H-ORNITH-SANDBOX-GAPS fixes.

Covers: path confinement on export out_path + subprocess sandbox via rlimits.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.ornith.sandbox import (
    confine_export_path,
    ornith_sandbox_preexec,
)
from general_ludd.ornith.training_data import TrainingDataCollector
from general_ludd.ornith.training_repo import OrnithTrainingRepo


class TestConfineExportPath:
    def test_none_out_path_returns_default_in_allowed_root(self):
        with patch(
            "general_ludd.ornith.sandbox._ORNITH_EXPORT_ROOT", "/tmp/ornith-exports"
        ):
            result = confine_export_path(None, "dataset.jsonl")
            assert result == Path("/tmp/ornith-exports/dataset.jsonl")


    def test_path_within_allowed_root_resolves(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [td]
        ):
            result = confine_export_path(f"{td}/my-export.jsonl", "fallback.jsonl")
            assert result == Path(td).resolve() / "my-export.jsonl"

    def test_platform_private_tmp_alias_is_allowed_when_present(self):
        private_tmp = Path("/private/tmp")
        if not private_tmp.exists():
            pytest.skip("platform does not expose /private/tmp")
        with patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [str(private_tmp)]
        ):
            result = confine_export_path(private_tmp / "ornith-export.jsonl", "fallback.jsonl")
        assert result == private_tmp / "ornith-export.jsonl"

    def test_path_outside_allowed_roots_raises_valueerror(self):
        with patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", ["/tmp"]
        ), pytest.raises(ValueError, match="not within an allowed export root"):
            confine_export_path("/etc/passwd", "fallback.jsonl")

    def test_dotdot_traversal_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            allowed = Path(td) / "exports"
            allowed.mkdir()
            with patch(
                "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [str(allowed)]
            ), pytest.raises(ValueError, match="not within an allowed export root"):
                confine_export_path(f"{allowed}/../escape.jsonl", "fallback.jsonl")

    def test_absolute_path_outside_root_is_blocked(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [td]
        ), pytest.raises(ValueError, match="not within an allowed export root"):
            confine_export_path("/etc/hosts", "fallback.jsonl")

    def test_symlink_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            allowed = Path(td) / "exports"
            allowed.mkdir()
            escape_target = Path(td) / "outside" / "escaped.jsonl"
            escape_target.parent.mkdir()
            escape_target.write_text("escaped")
            link = allowed / "safe-link"
            link.symlink_to(escape_target)
            with patch(
                "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [str(allowed)]
            ), pytest.raises(ValueError, match="not within an allowed export root"):
                confine_export_path(str(link), "fallback.jsonl")

    def test_relative_path_appended_to_export_root(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "general_ludd.ornith.sandbox._ORNITH_EXPORT_ROOT", td
        ), patch(
            "general_ludd.ornith.sandbox._ALLOWED_EXPORT_ROOTS", [td]
        ):
            result = confine_export_path("subdir/file.jsonl", "fallback.jsonl")
            assert result == Path(td).resolve() / "subdir/file.jsonl"

    def test_empty_out_path_returns_default(self):
        with patch(
            "general_ludd.ornith.sandbox._ORNITH_EXPORT_ROOT", "/tmp/ornith-exports"
        ):
            result = confine_export_path(None, "dataset.jsonl")
            assert result == Path("/tmp/ornith-exports/dataset.jsonl")


class TestOrnithSandboxPreexec:
    def test_preexec_does_not_raise(self):
        with patch("general_ludd.system.rlimit.apply_limits"):
            ornith_sandbox_preexec()

    def test_preexec_calls_apply_limits(self):
        with patch(
            "general_ludd.system.rlimit.apply_limits"
        ) as mock_apply:
            ornith_sandbox_preexec()
            assert mock_apply.called

    def test_preexec_passes_config_values(self):
        with patch(
            "general_ludd.system.rlimit.apply_limits"
        ) as mock_apply, patch(
            "general_ludd.ornith.sandbox.ORNITH_SANDBOX_MEM_MB", 2048
        ), patch(
            "general_ludd.ornith.sandbox.ORNITH_SANDBOX_CPU_S", 120
        ):
            ornith_sandbox_preexec()
            mock_apply.assert_called_once_with(2048, 120)

    def test_preexec_swallows_exceptions(self):
        with patch(
            "general_ludd.system.rlimit.apply_limits",
            side_effect=RuntimeError("setrlimit denied"),
        ):
            ornith_sandbox_preexec()

    def test_preexec_uses_defaults_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "general_ludd.system.rlimit.apply_limits"
        ) as mock_apply, patch(
            "general_ludd.ornith.sandbox.ORNITH_SANDBOX_MEM_MB", 4096
        ), patch(
            "general_ludd.ornith.sandbox.ORNITH_SANDBOX_CPU_S", 300
        ):
            ornith_sandbox_preexec()
            mock_apply.assert_called_once_with(4096, 300)


class TestTrainingRepoExportConfinement:
    async def test_export_dataset_passes_out_path_to_confinement(self):
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "dataset.jsonl"
            with patch(
                "general_ludd.ornith.training_repo.confine_export_path"
            ) as mock_confine:
                mock_confine.return_value = out_file
                mock_session = _async_mock_session()
                repo = OrnithTrainingRepo(mock_session)
                await repo.export_dataset(out_path=f"{td}/dataset.jsonl")
                mock_confine.assert_called_once()


class TestTrainingDataCollectorConfinement:
    async def test_export_finetuning_dataset_uses_confinement(self):
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "ft.jsonl"
            with patch(
                "general_ludd.ornith.training_data.confine_export_path"
            ) as mock_confine:
                mock_confine.return_value = out_file
                mock_session = _async_mock_session()
                collector = TrainingDataCollector(mock_session)
                await collector.export_finetuning_dataset(out_path=f"{td}/ft.jsonl")
                mock_confine.assert_called_once()

    async def test_export_rollout_log_uses_confinement(self):
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "rollout.jsonl"
            with patch(
                "general_ludd.ornith.training_data.confine_export_path"
            ) as mock_confine:
                mock_confine.return_value = out_file
                mock_session = _async_mock_session()
                collector = TrainingDataCollector(mock_session)
                await collector.export_rollout_log(out_path=f"{td}/rollout.jsonl")
                mock_confine.assert_called_once()


class TestD27SecurityBacklogCheck:
    def test_security_backlog_d27_checks_ornith(self):
        from general_ludd.security.security_backlog import _check_d27_sandbox_limits

        ok, msg = _check_d27_sandbox_limits()
        assert ok, msg
        assert "ornith.mcp_server" in msg


def _async_mock_session():
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result
    return session
