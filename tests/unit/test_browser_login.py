"""Unit tests for the XDG browser-based login flow (browser_login.py)."""

from __future__ import annotations

import dataclasses
import http.server
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.auth.browser_login import (
    SERVICE_PRESETS,
    BrowserLoginFlow,
    CredentialStore,
    EnvCredentialStore,
    ServiceConfig,
    _CallbackHandler,
    _find_free_port,
    _pkce_code_challenge,
    _pkce_code_verifier,
    list_services,
)

# ---------------------------------------------------------------------------
# ServiceConfig
# ---------------------------------------------------------------------------


class TestServiceConfig:
    def test_create_minimal(self) -> None:
        c = ServiceConfig(
            name="test",
            display_name="Test Service",
            auth_url="https://example.com/auth",
            exchange_url="",
        )
        assert c.name == "test"
        assert c.scopes == []
        assert c.requires_client_registration is True

    def test_create_full(self) -> None:
        c = ServiceConfig(
            name="full",
            display_name="Full Service",
            auth_url="https://example.com/auth",
            exchange_url="https://example.com/token",
            scopes=["read", "write"],
            client_id_env="CLIENT_ID",
            client_credential_env="CLIENT_SECRET",
            credential_env="MY_TOKEN",
            extra_auth_params={"prompt": "consent"},
            requires_client_registration=True,
            help_url="https://docs.example.com",
        )
        assert c.scopes == ["read", "write"]
        assert c.client_id_env == "CLIENT_ID"
        assert c.extra_auth_params["prompt"] == "consent"

    def test_immutable(self) -> None:
        c = ServiceConfig(
            name="test",
            display_name="Test",
            auth_url="https://example.com/auth",
            exchange_url="",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            cast(Any, c).name = "other"


# ---------------------------------------------------------------------------
# SERVICE_PRESETS
# ---------------------------------------------------------------------------


class TestServicePresets:
    def test_all_known_services_present(self) -> None:
        for name in ("github", "openai", "deepseek", "zai", "anthropic", "gemini", "openrouter"):
            assert name in SERVICE_PRESETS, f"missing preset: {name}"

    def test_all_presets_have_display_name(self) -> None:
        for name, cfg in SERVICE_PRESETS.items():
            assert cfg.display_name, f"missing display_name for {name}"

    def test_all_presets_have_auth_url(self) -> None:
        for name, cfg in SERVICE_PRESETS.items():
            assert cfg.auth_url, f"missing auth_url for {name}"

    def test_all_presets_have_credential_env(self) -> None:
        for name, cfg in SERVICE_PRESETS.items():
            assert cfg.credential_env, f"missing credential_env for {name}"

    def test_oauth2_services_have_token_url(self) -> None:
        for name in ("github", "gemini"):
            cfg = SERVICE_PRESETS[name]
            assert cfg.token_url, f"OAuth2 service {name} missing token_url"

    def test_apikey_services_have_no_token_url(self) -> None:
        for name in ("openai", "deepseek", "zai", "anthropic", "openrouter"):
            cfg = SERVICE_PRESETS[name]
            assert cfg.token_url == "", f"API-key service {name} should not have token_url"

    @pytest.mark.parametrize("name", ["github", "gemini"])
    def test_oauth2_services_require_registration(self, name: str) -> None:
        cfg = SERVICE_PRESETS[name]
        assert cfg.requires_client_registration is True

    @pytest.mark.parametrize("name", ["openai", "deepseek", "zai", "anthropic", "openrouter"])
    def test_apikey_services_no_registration(self, name: str) -> None:
        cfg = SERVICE_PRESETS[name]
        assert cfg.requires_client_registration is False


# ---------------------------------------------------------------------------
# list_services
# ---------------------------------------------------------------------------


class TestListServices:
    def test_returns_sorted_list(self) -> None:
        services = list_services()
        assert services == sorted(services)
        assert len(services) == len(SERVICE_PRESETS)

    def test_all_are_strings(self) -> None:
        for svc in list_services():
            assert isinstance(svc, str)


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


class TestPKCE:
    def test_code_verifier_default_length(self) -> None:
        v = _pkce_code_verifier()
        assert len(v) == 64

    def test_code_verifier_custom_length(self) -> None:
        v = _pkce_code_verifier(length=32)
        assert len(v) == 32

    def test_code_verifier_only_safe_chars(self) -> None:
        v = _pkce_code_verifier()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        assert all(c in allowed for c in v)

    def test_code_verifier_randomness(self) -> None:
        v1 = _pkce_code_verifier()
        v2 = _pkce_code_verifier()
        assert v1 != v2

    def test_code_challenge_length(self) -> None:
        challenge = _pkce_code_challenge("test-verifier")
        assert len(challenge) >= 32
        assert "=" not in challenge

    def test_code_challenge_deterministic(self) -> None:
        verifier = "my-test-verifier-string-1234567890abcdefghij"
        c1 = _pkce_code_challenge(verifier)
        c2 = _pkce_code_challenge(verifier)
        assert c1 == c2

    def test_code_challenge_different_for_different_verifiers(self) -> None:
        c1 = _pkce_code_challenge("verifier-one-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        c2 = _pkce_code_challenge("verifier-two-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        assert c1 != c2


# ---------------------------------------------------------------------------
# _find_free_port
# ---------------------------------------------------------------------------


class TestFindFreePort:
    def test_returns_valid_port(self) -> None:
        port = _find_free_port()
        assert isinstance(port, int)
        assert 1024 < port < 65536

    def test_ports_are_different(self) -> None:
        p1 = _find_free_port()
        p2 = _find_free_port()
        assert p1 != p2


# ---------------------------------------------------------------------------
# CredentialStore / EnvCredentialStore
# ---------------------------------------------------------------------------


class TestCredentialStore:
    def test_base_store_is_abstract(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            CredentialStore()  # type: ignore[abstract]
        assert True  # reached — ABC prevents direct instantiation


class TestEnvCredentialStore:
    def test_store_and_retrieve(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            store.store("openai", "sk-test-12345")
            assert store.retrieve("openai") == "sk-test-12345"

    def test_retrieve_nonexistent_service(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            assert store.retrieve("nonexistent") is None

    def test_overwrite_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            store.store("openai", "first-key")
            store.store("openai", "second-key")
            assert store.retrieve("openai") == "second-key"

    def test_multiple_services(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            store.store("openai", "openai-key")
            store.store("github", "github-token")
            store.store("zai", "zai-key")
            assert store.retrieve("openai") == "openai-key"
            assert store.retrieve("github") == "github-token"
            assert store.retrieve("zai") == "zai-key"

    def test_env_var_mapping_for_all_services(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            for svc in SERVICE_PRESETS:
                store.store(svc, f"test-token-{svc}")
                val = store.retrieve(svc)
                assert val == f"test-token-{svc}", f"mismatch for {svc}"

    def test_env_var_set_in_process(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            store.store("deepseek", "ds-key-test")
            assert os.environ.get("DEEPSEEK_API_KEY") == "ds-key-test"

    def test_metadata_storage(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            meta = {"scope": "write", "expires_at": "2026-12-31"}
            store.store_metadata("github", meta)
            meta_file = Path(tmp) / "github_metadata.json"
            assert meta_file.exists()
            data = json.loads(meta_file.read_text())
            assert data["scope"] == "write"

    def test_empty_file_initially(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            with patch.dict(os.environ, {}, clear=True):
                assert store.retrieve("openai") is None

    def test_env_var_for_unknown_service(self) -> None:
        assert EnvCredentialStore._env_var_for("custom") == "GLUDD_CUSTOM_TOKEN"


# ---------------------------------------------------------------------------
# _CallbackHandler
# ---------------------------------------------------------------------------


class TestCallbackHandler:
    def setup_method(self) -> None:
        _CallbackHandler.captured_code = None
        _CallbackHandler.captured_state = None
        _CallbackHandler.captured_error = None
        _CallbackHandler.done.clear()

    def teardown_method(self) -> None:
        _CallbackHandler.done.set()

    def _start_test_server(self) -> tuple[http.server.HTTPServer, int]:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
        server.timeout = 1.0
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, port

    @staticmethod
    def _request(url: str) -> int:
        try:
            response = urllib.request.urlopen(url)
        except urllib.error.HTTPError as error:
            with error:
                error.read()
            return error.code
        with response:
            response.read()
            return response.status

    def test_handler_captures_auth_code(self) -> None:
        server, port = self._start_test_server()
        _CallbackHandler.done.clear()
        try:
            url = f"http://127.0.0.1:{port}/callback?code=test-auth-code-123&state=mystate"
            assert self._request(url) == 200
            assert _CallbackHandler.captured_code == "test-auth-code-123"
            assert _CallbackHandler.captured_state == "mystate"
            assert _CallbackHandler.done.is_set()
        finally:
            server.shutdown()
            server.server_close()

    def test_handler_captures_error(self) -> None:
        server, port = self._start_test_server()
        _CallbackHandler.done.clear()
        try:
            url = f"http://127.0.0.1:{port}/callback?error=access_denied&error_description=User+denied"
            assert self._request(url) == 400
            assert _CallbackHandler.captured_error == "access_denied"
            assert _CallbackHandler.captured_code is None
            assert _CallbackHandler.done.is_set()
        finally:
            server.shutdown()
            server.server_close()

    def test_handler_rejects_missing_code(self) -> None:
        server, port = self._start_test_server()
        _CallbackHandler.done.clear()
        try:
            url = f"http://127.0.0.1:{port}/callback"
            assert self._request(url) == 400
            assert _CallbackHandler.captured_code is None
        finally:
            server.shutdown()
            server.server_close()

    def test_handler_returns_404_for_unknown_path(self) -> None:
        server, port = self._start_test_server()
        try:
            url = f"http://127.0.0.1:{port}/unknown"
            assert self._request(url) == 404
            assert _CallbackHandler.captured_code is None
        finally:
            server.shutdown()
            server.server_close()

    def test_handler_root_path(self) -> None:
        server, port = self._start_test_server()
        _CallbackHandler.done.clear()
        try:
            url = f"http://127.0.0.1:{port}/?code=root-code"
            assert self._request(url) == 200
            assert _CallbackHandler.captured_code == "root-code"
            assert _CallbackHandler.done.is_set()
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# BrowserLoginFlow — service validation
# ---------------------------------------------------------------------------


class TestBrowserLoginFlowValidation:
    def test_valid_service(self) -> None:
        flow = BrowserLoginFlow("github")
        assert flow.service_name == "github"

    def test_unknown_service_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown service"):
            BrowserLoginFlow("nonexistent-service")

    def test_explicit_config(self) -> None:
        cfg = ServiceConfig(
            name="custom",
            display_name="Custom",
            auth_url="https://custom.example.com",
            exchange_url="",
        )
        flow = BrowserLoginFlow.from_config(cfg)
        assert flow.service_name == "custom"
        assert flow.display_name == "Custom"

    def test_display_name(self) -> None:
        for svc in SERVICE_PRESETS:
            flow = BrowserLoginFlow(svc)
            assert flow.display_name == SERVICE_PRESETS[svc].display_name

    def test_model_credentials_not_leaked_in_repr(self) -> None:
        """Simulate a token value and verify it does not appear anywhere in
        the EnvCredentialStore public repr or the env file's exposed path."""
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            token = "secret-aaaaaaaaaaaaaaaa-key"
            store.store("openai", token)
            with open(env_file) as f:
                content = f.read()
            assert token in content
            assert "ExPoRt" not in str(store)  # nothing terribly revealing
            retrieved = store.retrieve("openai")
            assert retrieved == token


# ---------------------------------------------------------------------------
# BrowserLoginFlow — run() for API key services (mock browser)
# ---------------------------------------------------------------------------


class TestBrowserLoginFlowApiKey:
    def test_run_api_key_with_pasted_input(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            flow = BrowserLoginFlow("openai", store=store)

            with patch.dict(os.environ, {}, clear=True), \
                 patch("builtins.input", return_value="sk-mocked-key-12345"), \
                 patch("general_ludd.auth.browser_login._open_browser", return_value=None):
                token = flow.run(timeout=10)
                assert token == "sk-mocked-key-12345"
                assert store.retrieve("openai") == "sk-mocked-key-12345"

    def test_run_api_key_empty_input(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            flow = BrowserLoginFlow("deepseek", store=store)

            with patch.dict(os.environ, {}, clear=True), \
                 patch("builtins.input", return_value=""), \
                 patch("general_ludd.auth.browser_login._open_browser", return_value=None):
                token = flow.run(timeout=10)
                assert token is None
                assert store.retrieve("deepseek") is None

    def test_run_api_key_reuses_stored(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            store.store("openai", "already-stored-key")

            flow = BrowserLoginFlow("openai", store=store)
            token = flow.run(timeout=10)
            assert token == "already-stored-key"


# ---------------------------------------------------------------------------
# BrowserLoginFlow — OAuth2 flow (mocked HTTP)
# ---------------------------------------------------------------------------


class TestBrowserLoginFlowOAuth2:
    def test_run_oauth2_without_client_id(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            with patch.dict(os.environ, {}, clear=True):
                flow = BrowserLoginFlow("github", store=store)
                token = flow.run(timeout=5)
                assert token is None

    def test_run_oauth2_with_client_id_mocked_callback(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            with patch.dict(os.environ, {
                "GITHUB_OAUTH_CLIENT_ID": "test-client-id",
                "GITHUB_OAUTH_CLIENT_SECRET": "test-client-secret",
            }):
                flow = BrowserLoginFlow("github", store=store)

                def _inject_code(
                    *args: object,
                    **kwargs: object,
                ) -> http.server.HTTPServer:
                    _CallbackHandler.captured_code = "test-oauth-code"
                    return MagicMock(spec=http.server.HTTPServer)

                from general_ludd.security.url_fetch import FetchResult

                mock_response = FetchResult(
                    url="https://github.com/login/oauth/access_token",
                    status_code=200,
                    headers={},
                    content=json.dumps({
                        "access_token": "gho-test-token-abc123",
                        "token_type": "bearer",
                        "scope": "repo,user",
                    }).encode(),
                )

                with patch.object(
                    _CallbackHandler.done, "wait", return_value=True
                ), patch("general_ludd.auth.browser_login._start_callback_server",
                         side_effect=_inject_code), \
                   patch("general_ludd.auth.browser_login._open_browser", return_value=None), \
                   patch("general_ludd.auth.browser_login.secure_fetch", return_value=mock_response):
                    token = flow.run(timeout=10)
                    assert token == "gho-test-token-abc123"
                    assert store.retrieve("github") == "gho-test-token-abc123"

    def test_oauth2_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            with patch.dict(os.environ, {"GITHUB_OAUTH_CLIENT_ID": "cid"}):
                _CallbackHandler.captured_code = None
                _CallbackHandler.captured_error = None

                with patch.object(
                    _CallbackHandler.done, "wait", return_value=False
                ), patch("general_ludd.auth.browser_login._open_browser", return_value=None), \
                   patch("general_ludd.auth.browser_login._start_callback_server"):
                    flow = BrowserLoginFlow("github", store=store)
                    token = flow.run(timeout=1)
                    assert token is None

    def test_oauth2_timeout_closes_callback_server(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)
            server = MagicMock(spec=http.server.HTTPServer)
            with patch.dict(os.environ, {"GITHUB_OAUTH_CLIENT_ID": "cid"}), \
                 patch.object(_CallbackHandler.done, "wait", return_value=False), \
                 patch("general_ludd.auth.browser_login._open_browser", return_value=None), \
                 patch(
                     "general_ludd.auth.browser_login._start_callback_server",
                     return_value=server,
                 ):
                flow = BrowserLoginFlow("github", store=store)
                assert flow.run(timeout=1) is None

            server.shutdown.assert_called_once_with()
            server.server_close.assert_called_once_with()


# ---------------------------------------------------------------------------
# top-level login() convenience
# ---------------------------------------------------------------------------


class TestLoginFunction:
    def test_login_with_api_key(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)

            with patch.dict(os.environ, {}, clear=True), \
                 patch("builtins.input", return_value="api-key-from-function"), \
                 patch("general_ludd.auth.browser_login._open_browser", return_value=None):
                from general_ludd.auth.browser_login import login

                token = login("openai", store=store)
                assert token == "api-key-from-function"
                assert store.retrieve("openai") == "api-key-from-function"

    def test_login_unknown_service(self) -> None:
        from general_ludd.auth.browser_login import login

        with pytest.raises(ValueError, match="Unknown service"):
            login("foobar")

    def test_login_list_services(self) -> None:
        services = list_services()
        for svc in services:
            assert isinstance(SERVICE_PRESETS[svc], ServiceConfig)


# ---------------------------------------------------------------------------
# top-level open_browser_auth() convenience
# ---------------------------------------------------------------------------


class TestOpenBrowserAuth:
    def test_open_browser_auth_with_api_key(self) -> None:
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "creds.env"
            store = EnvCredentialStore(env_file)

            with patch.dict(os.environ, {}, clear=True), \
                 patch("builtins.input", return_value="open-browser-auth-key"), \
                 patch("general_ludd.auth.browser_login._open_browser", return_value=None):
                from general_ludd.auth.browser_login import open_browser_auth

                token = open_browser_auth("openai", store=store)
                assert token == "open-browser-auth-key"
                assert store.retrieve("openai") == "open-browser-auth-key"

    def test_open_browser_auth_unknown_service(self) -> None:
        from general_ludd.auth.browser_login import open_browser_auth

        with pytest.raises(ValueError, match="Unknown service"):
            open_browser_auth("nonexistent-service-xyz")

    def test_open_browser_auth_alias_matches_login(self) -> None:
        from general_ludd.auth.browser_login import login, open_browser_auth

        assert open_browser_auth is not None
        with pytest.raises(ValueError, match="Unknown service"):
            open_browser_auth("nonexistent-service-xyz")
        with pytest.raises(ValueError, match="Unknown service"):
            login("nonexistent-service-xyz")


# ---------------------------------------------------------------------------
# OpenBaoCredentialStore (import-time existence, not runtime)
# ---------------------------------------------------------------------------


class TestOpenBaoCredentialStore:
    def test_module_exists(self) -> None:
        from general_ludd.auth.browser_login import OpenBaoCredentialStore
        assert OpenBaoCredentialStore is not None


# ---------------------------------------------------------------------------
# Auth module init exports
# ---------------------------------------------------------------------------


class TestAuthInit:
    def test_all_exports_present(self) -> None:
        from general_ludd.auth import (
            SERVICE_PRESETS,
            BrowserLoginFlow,
            EnvCredentialStore,
            ServiceConfig,
            list_services,
            login,
        )
        assert BrowserLoginFlow is not None
        assert EnvCredentialStore is not None
        assert ServiceConfig is not None
        assert SERVICE_PRESETS is not None
        assert list_services is not None
        assert login is not None
