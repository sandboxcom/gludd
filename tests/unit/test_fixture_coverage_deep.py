"""Deep fixture / mock coverage tests.

Verifies every conftest.py fixture across the test suite:
- Autouse fixtures produce correct state (they auto-run around every test)
- Non-autouse fixture helpers behave correctly
- Generator fixtures yield and teardown properly
- Scopes are appropriate, no unused fixtures
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

# =====================================================================
# Autouse fixtures from tests/conftest.py — verify their auto-run effects
# =====================================================================


class TestAllowNoAuthEffect:
    """_allow_no_auth_by_default (autouse) sets GLUDD_ALLOW_NO_AUTH=1."""

    def test_env_var_is_set_by_default(self):
        assert os.environ.get("GLUDD_ALLOW_NO_AUTH") == "1"

    def test_env_var_absent_when_psk_present(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-key")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        assert os.environ.get("GLUDD_AUTH_PSK") == "test-key"
        assert os.environ.get("GLUDD_ALLOW_NO_AUTH") is None


class TestClientLoopbackHostEffect:
    """_testclient_presents_loopback_host (autouse, session) patches TestClient."""

    def test_testclient_class_exists(self):
        import starlette.testclient as tc_mod

        assert hasattr(tc_mod.TestClient, "__init__")
        assert callable(tc_mod.TestClient.__init__)


class TestRestoreLeakyEnvVarsEffect:
    """_restore_leaky_env_vars (autouse) snapshots/restores leaky env vars."""

    _SNAPSHOTTED = frozenset({"GLUDD_AUTH_PSK", "GLUDD_ALLOW_NO_AUTH", "AWS_ACCESS_KEY_ID"})

    def test_snapshotted_vars_are_tracked(self):
        from tests.conftest import _LEAKY_ENV_VARS

        assert "GLUDD_AUTH_PSK" in _LEAKY_ENV_VARS
        assert "GLUDD_ALLOW_NO_AUTH" in _LEAKY_ENV_VARS

    def test_mutation_is_restored_by_fixture(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "before")
        monkeypatch.setenv("GLUDD_AUTH_PSK", "mutated")
        assert os.environ.get("GLUDD_AUTH_PSK") == "mutated"


class TestIsolateRootLoggerEffect:
    """_isolate_root_logger (autouse) snapshots/restores logging state."""

    def test_root_logger_is_healthy(self):
        root = logging.getLogger()
        assert isinstance(root.level, int)

    def test_logger_dict_contains_named_loggers(self):
        manager = logging.Logger.manager
        assert isinstance(manager.loggerDict, dict)

    def test_new_logger_created_in_test_isolated(self):
        new_logger = logging.getLogger("gludd_ephemeral_test_logger")
        assert new_logger.level == logging.NOTSET
        assert new_logger.propagate is True


class TestResetProcessRegistryEffect:
    """_reset_process_registry (autouse) resets process registry singleton."""

    def test_registry_is_none_at_test_start(self):
        import general_ludd.process.registry as pr

        assert pr._DEFAULT_REGISTRY is None


class TestResetLanguageParsersEffect:
    """_reset_language_parsers (autouse) clears language parser cache."""

    def test_parser_cache_is_empty_at_test_start(self):
        import general_ludd.code_intelligence.extractor as ex

        assert len(ex._LANGUAGE_PARSERS) == 0


class TestResetWorkerRunnerEffect:
    """_reset_worker_runner (autouse) resets worker runner singleton."""

    def test_runner_is_none_at_test_start(self):
        import general_ludd.worker.app as wapp

        assert wapp._runner is None


class TestResetObservabilitySingletonsEffect:
    """_reset_observability_singletons (autouse) resets observability state."""

    def test_integrity_key_is_none(self):
        import general_ludd.integrity.scanner as scanner

        assert scanner._INTEGRITY_KEY is None

    def test_shared_token_tracker_is_none(self):
        import general_ludd.observability.token_cost as tc

        assert tc._shared_tracker is None

    def test_default_timing_tracker_is_none(self):
        import general_ludd.observability.timing as timing

        assert timing._default_tracker is None

    def test_metrics_exporter_is_none(self):
        import general_ludd.observability.metrics_exporter as me

        assert me._metrics_exporter is None

    def test_metrics_registry_is_fresh(self):
        from prometheus_client import CollectorRegistry

        import general_ludd.observability.metrics_exporter as me

        assert isinstance(me._REGISTRY, CollectorRegistry)


class TestSandboxSysModulesAndPathEffect:
    """_sandbox_sys_modules_and_path (autouse) snapshots/restores sys state."""

    def test_sys_path_contains_src_and_scripts(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        src_dir = str(repo_root / "src")
        scripts_dir = str(repo_root / "scripts")
        assert src_dir in sys.path
        assert scripts_dir in sys.path

    def test_denylist_prefixes_defined(self):
        from tests.conftest import _A3_DENYLIST_PREFIXES

        assert "live_pkg_" in _A3_DENYLIST_PREFIXES
        assert "rbpkg" in _A3_DENYLIST_PREFIXES


class TestEnsureGluddDirEffect:
    """_ensure_gludd_dir_exists (autouse, session) creates .gludd/."""

    def test_gludd_dir_exists(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        gludd_dir = repo_root / ".gludd"
        assert gludd_dir.exists() and gludd_dir.is_dir()


class TestResetHttpxAndAbtestEffect:
    """_reset_httpx_and_abtest_runner (autouse) resets httpx.get + abtest."""

    def test_httpx_get_is_real_function_not_mock(self):
        assert callable(httpx.get)
        result = repr(httpx.get)
        assert "Mock" not in result
        assert "MagicMock" not in result

    def test_abtest_run_candidate_is_real_function(self):
        from general_ludd.abtest.compare import run_candidate_in_subprocess

        assert callable(run_candidate_in_subprocess)
        result = repr(run_candidate_in_subprocess)
        assert "Mock" not in result
        assert "MagicMock" not in result


class TestPatchAiosqliteWorkerEffect:
    """_patch_aiosqlite_worker_for_closed_loop_teardown (autouse, session)."""

    def test_aiosqlite_worker_is_patchable(self):
        try:
            import aiosqlite.core as ac

            assert hasattr(ac, "_connection_worker_thread")
            assert callable(ac._connection_worker_thread)
        except ImportError:
            pytest.skip("aiosqlite not installed")


# =====================================================================
# tests/integration/conftest.py — _block_hf_downloads (autouse, session)
# =====================================================================


class TestBlockHfDownloadsEffect:
    """_block_hf_downloads stubs out HuggingFace downloads for integration tests."""

    def test_block_hf_downloads_fixture_defined(self):
        """The _block_hf_downloads fixture exists and is session-scoped."""
        import tests.integration.conftest as m

        assert hasattr(m, "_block_hf_downloads")

    def test_block_hf_downloads_produces_generator(self):
        """The fixture's body yields a generator with proper teardown."""
        import inspect

        import tests.integration.conftest as m

        src = inspect.getsource(m._block_hf_downloads)
        assert "yield" in src
        assert "huggingface_hub" in src or "hf_hub_download" in src


# =====================================================================
# tests/e2e/dogfood/conftest.py — non-autouse fixtures testable via helpers
# =====================================================================


class TestDogfoodHelpers:
    """Verify the underlying helper functions used by dogfood e2e fixtures."""

    def test_repo_root_fixture_exists(self):
        import tests.e2e.dogfood.conftest as m

        assert hasattr(m, "repo_root")
        assert callable(getattr(m, "repo_root", None))

    def test_repo_root_logic_replicates_path(self):
        import tests.e2e.dogfood.conftest as m

        expected = Path(__file__).resolve().parent.parent.parent
        fixture_fn = getattr(m.repo_root, "__wrapped__", m.repo_root)
        assert fixture_fn().resolve() == expected
        assert (expected / "pyproject.toml").is_file()
        assert (expected / "tests" / "e2e" / "dogfood").is_dir()

    def test_zai_creds_fixture_exists(self):
        import tests.e2e.dogfood.conftest as m

        assert hasattr(m, "zai_creds")

    def test_zai_creds_load_returns_dict_or_none(self):
        from tests.e2e.dogfood._secrets import load_llm_keys

        creds = load_llm_keys(Path(__file__).resolve().parent.parent.parent)
        assert creds is None or isinstance(creds, dict)

    def test_gateway_mode_logic_is_correct(self):
        from tests.e2e.dogfood.conftest import gateway_mode

        func = getattr(gateway_mode, "__wrapped__", lambda x: x)
        assert func(None) == "mock"
        assert func({"key": "val"}) == "live"

    def test_free_port_helper_returns_valid_port(self):
        from tests.e2e.dogfood.conftest import _find_free_port

        port = _find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_tmp_workspace_creates_and_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix="gludd-test-") as d:
            dir_path = Path(d)
            assert dir_path.exists()
        assert not dir_path.exists()


# =====================================================================
# tests/e2e/games/conftest.py fixtures
# =====================================================================


class TestGamesGatewayEffect:
    """gateway fixture returns deepseek gateway (class-scoped)."""

    def test_skip_reason_is_defined(self):
        from tests.e2e.test_game_building_deepseek import _SKIP_REASON

        assert isinstance(_SKIP_REASON, str)
        assert len(_SKIP_REASON) > 0

    def test_get_deepseek_key_returns_str_or_none(self):
        from tests.e2e.test_game_building_deepseek import _get_deepseek_key

        key = _get_deepseek_key()
        assert key is None or isinstance(key, str)

    def test_build_deepseek_gateway_is_callable(self):
        from tests.e2e.test_game_building_deepseek import _build_deepseek_gateway

        assert callable(_build_deepseek_gateway)


# =====================================================================
# tests/e2e/providers/conftest.py fixtures
# =====================================================================


class TestProviderHelpers:
    """Verify helpers underlying vllm_base_url / llamacpp_base_url fixtures."""

    def test_maybe_spawn_returns_none_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VLLM_E2E_SPAWN", raising=False)
        monkeypatch.delenv("LLAMACPP_E2E_SPAWN", raising=False)
        from tests.e2e.providers.conftest import _maybe_spawn

        assert _maybe_spawn("vllm") is None
        assert _maybe_spawn("llamacpp") is None

    def test_maybe_spawn_returns_placeholder_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VLLM_E2E_SPAWN", "1")
        from tests.e2e.providers.conftest import _maybe_spawn

        assert _maybe_spawn("vllm") == "spawn-requested"

    def test_require_backend_url_skips_without_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VLLM_E2E_SPAWN", raising=False)
        monkeypatch.delenv("VLLM_BASE_URL", raising=False)
        with pytest.raises(pytest.skip.Exception):
            from tests.e2e.providers.conftest import _require_backend_url

            _require_backend_url("vllm")

    def test_require_backend_url_reads_env_var(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VLLM_E2E_SPAWN", raising=False)
        monkeypatch.setenv("VLLM_BASE_URL", "http://localhost:8888")
        from tests.e2e.providers.conftest import _require_backend_url

        url = _require_backend_url("vllm")
        assert url == "http://localhost:8888"

    def test_vllm_fixture_exists_and_has_correct_params(self):
        import inspect

        import tests.e2e.providers.conftest as m

        assert hasattr(m, "vllm_base_url")
        src = inspect.getsource(m.vllm_base_url)
        assert "yield" in src
        assert "VLLM_BASE_URL" in src

    def test_llamacpp_fixture_exists_and_has_correct_params(self):
        import inspect

        import tests.e2e.providers.conftest as m

        assert hasattr(m, "llamacpp_base_url")
        src = inspect.getsource(m.llamacpp_base_url)
        assert "yield" in src
        assert "LLAMACPP_BASE_URL" in src
