"""Tests for module_utils/model_client.py — ModelClient for daemon model gateway."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch


def _import_module() -> ModuleType:
    sys.path.insert(
        0,
        "collections/ansible_collections/general_ludd/agent/plugins",
    )
    try:
        from module_utils import model_client

        return model_client
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# Env-var resolution helpers
# ---------------------------------------------------------------------------


class TestEnvResolution:
    def test_default_daemon_url(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            assert mod._env_daemon_url() == mod.DEFAULT_DAEMON_URL

    def test_gludd_daemon_url_env(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_DAEMON_URL": "http://daemon:9000"}, clear=True):
            assert mod._env_daemon_url() == "http://daemon:9000"

    def test_daemon_url_fallback(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"DAEMON_URL": "http://fallback:9000"}, clear=True):
            assert mod._env_daemon_url() == "http://fallback:9000"

    def test_gludd_daemon_url_priority(self) -> None:
        mod = _import_module()
        with patch.dict(
            "os.environ",
            {"GLUDD_DAEMON_URL": "http://primary:9000", "DAEMON_URL": "http://fallback:9000"},
            clear=True,
        ):
            assert mod._env_daemon_url() == "http://primary:9000"

    def test_default_psk_empty(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            assert mod._env_psk() == ""

    def test_psk_from_env(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_PSK": "secret-key-123"}, clear=True):
            assert mod._env_psk() == "secret-key-123"

    def test_default_timeout(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            assert mod._env_timeout() == mod.DEFAULT_TIMEOUT

    def test_custom_timeout(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_MODEL_TIMEOUT": "60"}, clear=True):
            assert mod._env_timeout() == 60

    def test_invalid_timeout_falls_back(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_MODEL_TIMEOUT": "abc"}, clear=True):
            assert mod._env_timeout() == mod.DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# ModelClient construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_profile(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        assert client._profile == "default"
        assert client._base_url == mod.DEFAULT_DAEMON_URL
        assert client._psk == ""
        assert client._timeout == mod.DEFAULT_TIMEOUT

    def test_custom_profile(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient(profile_name="openai-fast")
        assert client._profile == "openai-fast"

    def test_psk_auth_headers(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_PSK": "key-abc"}, clear=True):
            client = mod.ModelClient()
        headers = client._headers()
        assert headers["Authorization"] == "Bearer key-abc"
        assert headers["X-PSK"] == "key-abc"

    def test_no_psk_no_auth_headers(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        headers = client._headers()
        assert "Authorization" not in headers
        assert "X-PSK" not in headers


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


class TestUrlBuilding:
    def test_url_simple_path(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        assert client._url("/admin/models") == "http://localhost:8000/admin/models"

    def test_url_path_strips_slash(self) -> None:
        mod = _import_module()
        with patch.dict("os.environ", {"GLUDD_DAEMON_URL": "http://host:8888/"}, clear=True):
            client = mod.ModelClient()
        assert client._url("/admin/models") == "http://host:8888/admin/models"


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------


class TestChat:
    @patch("urllib.request.urlopen")
    def test_simple_user_message(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            {
                "text": "Hello, world!",
                "model_profile_id": "default",
                "usage": {"total_tokens": 10},
            }
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.chat([{"role": "user", "content": "Hi!"}])
        assert result["text"] == "Hello, world!"
        assert result["model_profile_id"] == "default"
        assert result["_status"] == 200

    def test_system_and_user_message(self) -> None:
        mod = _import_module()
        captured: list[bytes] = []

        class FakeResp:
            status = 200

            def read(self) -> bytes:
                return json.dumps({"text": "ok"}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a: object) -> None:
                pass

        def fake_urlopen(req: object, timeout: int = 30) -> FakeResp:
            captured.append(req.data)  # type: ignore[attr-defined]
            return FakeResp()

        with patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict("os.environ", {}, clear=True):
                client = mod.ModelClient()
            client.chat(
                [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Hi!"},
                ]
            )

        body = json.loads(captured[0].decode("utf-8"))
        assert body["prompt"] == "Hi!"
        assert body["system"] == "Be helpful."

    def test_passes_model_profile_for_non_default(self) -> None:
        mod = _import_module()
        captured: list[bytes] = []

        class FakeResp:
            status = 200

            def read(self) -> bytes:
                return json.dumps({"text": "ok"}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a: object) -> None:
                pass

        def fake_urlopen(req: object, timeout: int = 30) -> FakeResp:
            captured.append(req.data)  # type: ignore[attr-defined]
            return FakeResp()

        with patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict("os.environ", {}, clear=True):
                client = mod.ModelClient(profile_name="gpt4")
            client.chat([{"role": "user", "content": "Test"}])

        body = json.loads(captured[0].decode("utf-8"))
        assert body["model_profile"] == "gpt4"

    def test_passes_kwargs(self) -> None:
        mod = _import_module()
        captured: list[bytes] = []

        class FakeResp:
            status = 200

            def read(self) -> bytes:
                return json.dumps({"text": "ok"}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a: object) -> None:
                pass

        def fake_urlopen(req: object, timeout: int = 30) -> FakeResp:
            captured.append(req.data)  # type: ignore[attr-defined]
            return FakeResp()

        with patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict("os.environ", {}, clear=True):
                client = mod.ModelClient()
            client.chat(
                [{"role": "user", "content": "Test"}],
                max_tokens=128,
                temperature=0.7,
            )

        body = json.loads(captured[0].decode("utf-8"))
        assert body["max_tokens"] == 128
        assert body["temperature"] == 0.7

    @patch("urllib.request.urlopen")
    def test_http_error(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_urlopen.side_effect = urllib_error("url", 503, "Down", {}, None)
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.chat([{"role": "user", "content": "Test"}])
        assert result["_status"] == 503

    @patch("urllib.request.urlopen")
    def test_url_error(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_urlopen.side_effect = urllib_urlerror("connection refused")
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.chat([{"role": "user", "content": "Test"}])
        assert result["_error"] == "connection refused"
        assert result["_status"] == 0


# ---------------------------------------------------------------------------
# chat_stream()
# ---------------------------------------------------------------------------


class TestChatStream:
    @patch("urllib.request.urlopen")
    def test_yields_events(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.__iter__ = MagicMock(
            return_value=iter(
                [
                    b'data: {"delta": "Hello"}\n',
                    b'data: {"delta": " world"}\n',
                    b"data: [DONE]\n",
                ]
            )
        )
        mock_urlopen.return_value = resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        events = list(client.chat_stream([{"role": "user", "content": "Say hi"}]))
        assert len(events) == 2
        assert events[0] == {"delta": "Hello"}
        assert events[1] == {"delta": " world"}

    @patch("urllib.request.urlopen")
    def test_empty_lines_skipped(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.__iter__ = MagicMock(
            return_value=iter(
                [
                    b"\n",
                    b'data: {"token": "abc"}\n',
                    b"\n",
                    b"\n",
                ]
            )
        )
        mock_urlopen.return_value = resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        events = list(client.chat_stream([{"role": "user", "content": "x"}]))
        assert len(events) == 1
        assert events[0] == {"token": "abc"}

    @patch("urllib.request.urlopen")
    def test_non_data_lines_skipped(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.__iter__ = MagicMock(
            return_value=iter(
                [
                    b":ping\n",
                    b'data: {"real": true}\n',
                ]
            )
        )
        mock_urlopen.return_value = resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        events = list(client.chat_stream([{"role": "user", "content": "x"}]))
        assert len(events) == 1
        assert events[0] == {"real": True}

    @patch("urllib.request.urlopen")
    def test_malformed_json_yields_raw(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.__iter__ = MagicMock(
            return_value=iter(
                [
                    b"data: not-json\n",
                ]
            )
        )
        mock_urlopen.return_value = resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        events = list(client.chat_stream([{"role": "user", "content": "x"}]))
        assert len(events) == 1
        assert events[0] == {"_raw": "not-json"}

    @patch("urllib.request.urlopen")
    def test_http_error_yields_error_event(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_urlopen.side_effect = urllib_error("url", 429, "Too Many", {}, None)
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        events = list(client.chat_stream([{"role": "user", "content": "x"}]))
        assert len(events) == 1
        assert events[0]["_status"] == 429


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------


class TestEmbed:
    @patch("urllib.request.urlopen")
    def test_single_text(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            {
                "query_embedding": [0.1, 0.2, 0.3],
                "query_embedding_dim": 3,
                "embedding_method": "hash",
                "results": [],
            }
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.embed("test text")
        assert result["embedding"] == [0.1, 0.2, 0.3]
        assert result["embedding_method"] == "hash"
        assert result["dim"] == 3

    @patch("urllib.request.urlopen")
    def test_multiple_texts(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        call_count = 0
        responses = [
            json.dumps(
                {
                    "query_embedding": [1.0, 0.0],
                    "query_embedding_dim": 2,
                    "embedding_method": "hash",
                    "results": [],
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "query_embedding": [0.0, 1.0],
                    "query_embedding_dim": 2,
                    "embedding_method": "hash",
                    "results": [],
                }
            ).encode("utf-8"),
        ]

        def fake_read() -> bytes:
            nonlocal call_count
            data = responses[call_count]
            call_count += 1
            return data

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = fake_read
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.embed(["first", "second"])
        assert result["embedding_method"] == "hash"
        assert result["dim"] == 2
        assert result["embeddings"] == [[1.0, 0.0], [0.0, 1.0]]

    @patch("urllib.request.urlopen")
    def test_no_embedding_in_response(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            {
                "query_embedding": None,
                "query_embedding_dim": 0,
                "embedding_method": "hash",
            }
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.embed("test")
        assert result["embedding"] is None
        assert result["dim"] == 0


# ---------------------------------------------------------------------------
# list_models()
# ---------------------------------------------------------------------------


class TestListModels:
    @patch("urllib.request.urlopen")
    def test_returns_profiles(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            {
                "profiles": [
                    {"model_profile_id": "default", "model_name": "gpt-4o"},
                    {"model_profile_id": "deepseek", "model_name": "deepseek-chat"},
                ],
            }
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.list_models()
        assert len(result["profiles"]) == 2
        assert result["profiles"][0]["model_profile_id"] == "default"
        assert result["_status"] == 200

    @patch("urllib.request.urlopen")
    def test_empty_profiles(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"profiles": []}).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        result = client.list_models()
        assert result["profiles"] == []


# ---------------------------------------------------------------------------
# reachable()
# ---------------------------------------------------------------------------


class TestReachable:
    @patch("urllib.request.urlopen")
    def test_healthy(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        assert client.reachable() is True

    @patch("urllib.request.urlopen")
    def test_unhealthy(self, mock_urlopen: MagicMock) -> None:
        mod = _import_module()
        mock_urlopen.side_effect = urllib_urlerror("no route")
        with patch.dict("os.environ", {}, clear=True):
            client = mod.ModelClient()
        assert client.reachable() is False


# ---------------------------------------------------------------------------
# Error helpers (imported once for use in exception side_effects)
# ---------------------------------------------------------------------------


def urllib_error(url: str, code: int, msg: str, hdrs: Any, fp: Any) -> Any:
    import urllib.error

    err = urllib.error.HTTPError(url=url, code=code, msg=msg, hdrs=hdrs, fp=fp)
    return err


def urllib_urlerror(reason: str) -> Any:
    import urllib.error

    return urllib.error.URLError(reason)
