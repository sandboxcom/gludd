"""Tests for module_utils/model_client.py — thin ModelGateway + HashEmbedder wrapper."""

from __future__ import annotations

import sys
from types import ModuleType
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
# Message helper
# ---------------------------------------------------------------------------


class TestMessage:
    def test_creates_role_content_dict(self) -> None:
        mod = _import_module()
        msg = mod.Message("user", "Hello")
        assert msg == {"role": "user", "content": "Hello"}

    def test_system_role(self) -> None:
        mod = _import_module()
        msg = mod.Message("system", "Be concise.")
        assert msg == {"role": "system", "content": "Be concise."}


# ---------------------------------------------------------------------------
# ModelClient construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_profile(self) -> None:
        mod = _import_module()
        with (
            patch.object(mod, "_get_gateway") as mock_gw,
            patch.object(mod, "HashEmbedder") as mock_embedder_cls,
        ):
            mock_embedder_cls.return_value = MagicMock()
            client = mod.ModelClient()
            assert client._profile == "default"
            mock_gw.assert_called_once()

    def test_custom_profile(self) -> None:
        mod = _import_module()
        with (
            patch.object(mod, "_get_gateway") as mock_gw,
            patch.object(mod, "HashEmbedder") as mock_embedder_cls,
        ):
            mock_embedder_cls.return_value = MagicMock()
            client = mod.ModelClient(profile_name="deepseek-fast")
            assert client._profile == "deepseek-fast"
            mock_gw.assert_called_once()

    def test_embedder_is_hash_embedder(self) -> None:
        mod = _import_module()
        with (
            patch.object(mod, "_get_gateway"),
            patch.object(mod, "HashEmbedder") as mock_embedder_cls,
        ):
            mock_embedder = MagicMock()
            mock_embedder_cls.return_value = mock_embedder
            client = mod.ModelClient()
            assert client._embedder is mock_embedder


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------


class TestChat:
    def test_delegates_to_gateway_call_model(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Hello, world!"
        mock_response.model_name = "deepseek-chat"
        mock_response.usage_metadata = {"total_tokens": 10}
        mock_response.cost_estimate = 0.001
        mock_gateway.call_model.return_value = mock_response
        mock_embedder = MagicMock()

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=mock_embedder),
        ):
            client = mod.ModelClient(profile_name="default")
            result = client.chat([{"role": "user", "content": "Hi!"}])

        assert result["text"] == "Hello, world!"
        assert result["model_profile_id"] == "default"
        assert result["model_name"] == "deepseek-chat"
        assert result["usage"] == {"total_tokens": 10}
        assert result["cost_estimate"] == 0.001
        assert result["_status"] == 200
        mock_gateway.call_model.assert_called_once_with(
            "default",
            [{"role": "user", "content": "Hi!"}],
        )

    def test_passes_kwargs_to_gateway(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.model_name = ""
        mock_response.usage_metadata = {}
        mock_response.cost_estimate = 0.0
        mock_gateway.call_model.return_value = mock_response

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            client.chat(
                [{"role": "user", "content": "Test"}],
                max_tokens=128,
                temperature=0.7,
            )

        mock_gateway.call_model.assert_called_once_with(
            "default",
            [{"role": "user", "content": "Test"}],
            max_tokens=128,
            temperature=0.7,
        )

    def test_uses_correct_profile(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "ok"
        mock_response.model_name = ""
        mock_response.usage_metadata = {}
        mock_response.cost_estimate = 0.0
        mock_gateway.call_model.return_value = mock_response

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient(profile_name="gpt4")
            client.chat([{"role": "user", "content": "Test"}])

        mock_gateway.call_model.assert_called_once_with(
            "gpt4",
            [{"role": "user", "content": "Test"}],
        )

    def test_gateway_error_propagates(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.call_model.side_effect = ValueError("profile not found")

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            import pytest

            with pytest.raises(ValueError, match="profile not found"):
                client.chat([{"role": "user", "content": "Test"}])


# ---------------------------------------------------------------------------
# chat_stream()
# ---------------------------------------------------------------------------


class TestChatStream:
    def test_yields_delta_dicts(self) -> None:
        mod = _import_module()

        chunk_a = MagicMock()
        chunk_a.content = "Hello"
        chunk_b = MagicMock()
        chunk_b.content = " world"
        chunk_c = MagicMock()
        chunk_c.content = ""

        mock_gateway = MagicMock()
        mock_gateway.call_model_stream.return_value = iter([chunk_a, chunk_b, chunk_c])

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            events = list(client.chat_stream([{"role": "user", "content": "Say hi"}]))

        assert len(events) == 2
        assert events[0] == {"delta": "Hello"}
        assert events[1] == {"delta": " world"}

    def test_empty_content_skipped(self) -> None:
        mod = _import_module()

        chunk = MagicMock()
        chunk.content = ""

        mock_gateway = MagicMock()
        mock_gateway.call_model_stream.return_value = iter([chunk])

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            events = list(client.chat_stream([{"role": "user", "content": "x"}]))

        assert len(events) == 0

    def test_non_string_content_converted(self) -> None:
        mod = _import_module()

        chunk = MagicMock()
        chunk.content = 42

        mock_gateway = MagicMock()
        mock_gateway.call_model_stream.return_value = iter([chunk])

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            events = list(client.chat_stream([{"role": "user", "content": "x"}]))

        assert len(events) == 1
        assert events[0] == {"delta": "42"}

    def test_passes_kwargs_to_gateway(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.call_model_stream.return_value = iter([])

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            list(
                client.chat_stream(
                    [{"role": "user", "content": "x"}],
                    temperature=0.5,
                    tools=[],
                )
            )

        mock_gateway.call_model_stream.assert_called_once_with(
            "default",
            [{"role": "user", "content": "x"}],
            temperature=0.5,
            tools=[],
        )

    def test_gateway_error_propagates(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.call_model_stream.side_effect = ValueError("profile not found")

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            import pytest

            with pytest.raises(ValueError, match="profile not found"):
                list(client.chat_stream([{"role": "user", "content": "x"}]))


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------


class TestEmbed:
    def test_single_text_delegates_to_hash_embedder(self) -> None:
        mod = _import_module()

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

        mock_gateway = MagicMock()

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=mock_embedder),
        ):
            client = mod.ModelClient()
            result = client.embed("test text")

        assert result["embedding"] == [0.1, 0.2, 0.3]
        assert result["embedding_method"] == "hash"
        assert result["dim"] == 3
        mock_embedder.embed.assert_called_once_with("test text")

    def test_multiple_texts(self) -> None:
        mod = _import_module()

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = [[1.0, 0.0], [0.0, 1.0]]

        with (
            patch.object(mod, "_get_gateway", return_value=MagicMock()),
            patch.object(mod, "HashEmbedder", return_value=mock_embedder),
        ):
            client = mod.ModelClient()
            result = client.embed(["first", "second"])

        assert result["embedding_method"] == "hash"
        assert result["dim"] == 2
        assert result["embeddings"] == [[1.0, 0.0], [0.0, 1.0]]
        assert mock_embedder.embed.call_count == 2

    def test_empty_list(self) -> None:
        mod = _import_module()

        mock_embedder = MagicMock()

        with (
            patch.object(mod, "_get_gateway", return_value=MagicMock()),
            patch.object(mod, "HashEmbedder", return_value=mock_embedder),
        ):
            client = mod.ModelClient()
            result = client.embed([])

        assert result["embeddings"] == []
        assert result["dim"] == 0
        mock_embedder.embed.assert_not_called()

    def test_single_text_empty_string(self) -> None:
        mod = _import_module()

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.0] * 256

        with (
            patch.object(mod, "_get_gateway", return_value=MagicMock()),
            patch.object(mod, "HashEmbedder", return_value=mock_embedder),
        ):
            client = mod.ModelClient()
            result = client.embed("")

        assert len(result["embedding"]) == 256
        assert result["dim"] == 256


# ---------------------------------------------------------------------------
# list_models()
# ---------------------------------------------------------------------------


class TestListModels:
    def test_returns_profiles_from_gateway(self) -> None:
        mod = _import_module()

        mock_profile_a = MagicMock()
        mock_profile_a.model_dump.return_value = {
            "model_profile_id": "default",
            "model_name": "deepseek-chat",
        }
        mock_profile_b = MagicMock()
        mock_profile_b.model_dump.return_value = {
            "model_profile_id": "openai-fast",
            "model_name": "gpt-4o-mini",
        }

        mock_gateway = MagicMock()
        mock_gateway.list_profiles.return_value = [mock_profile_a, mock_profile_b]

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            result = client.list_models()

        assert len(result["profiles"]) == 2
        assert result["profiles"][0]["model_profile_id"] == "default"
        assert result["profiles"][1]["model_name"] == "gpt-4o-mini"
        assert result["_status"] == 200
        mock_gateway.list_profiles.assert_called_once()

    def test_empty_profiles(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.list_profiles.return_value = []

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            result = client.list_models()

        assert result["profiles"] == []
        assert result["_status"] == 200


# ---------------------------------------------------------------------------
# reachable()
# ---------------------------------------------------------------------------


class TestReachable:
    def test_healthy_when_profiles_exist(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.list_profiles.return_value = [MagicMock()]

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            assert client.reachable() is True

    def test_unhealthy_when_no_profiles(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.list_profiles.return_value = []

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            assert client.reachable() is False

    def test_unhealthy_when_gateway_raises(self) -> None:
        mod = _import_module()

        mock_gateway = MagicMock()
        mock_gateway.list_profiles.side_effect = RuntimeError("boom")

        with (
            patch.object(mod, "_get_gateway", return_value=mock_gateway),
            patch.object(mod, "HashEmbedder", return_value=MagicMock()),
        ):
            client = mod.ModelClient()
            assert client.reachable() is False


# ---------------------------------------------------------------------------
# _get_gateway — singleton construction behaviour
# ---------------------------------------------------------------------------


class TestGetGateway:
    def test_returns_same_instance(self) -> None:
        mod = _import_module()
        gw1 = mod._get_gateway()
        gw2 = mod._get_gateway()
        assert gw1 is gw2

    def test_builds_gateway_from_env(self) -> None:
        mod = _import_module()
        from unittest.mock import patch as _patch

        with _patch.dict(
            "os.environ",
            {
                "GLUDD_MODEL_PROFILE_ID": "my-profile",
                "GLUDD_MODEL_PROVIDER": "openai",
                "GLUDD_MODEL_NAME": "gpt-4o",
            },
        ):
            # Reset singleton so env vars take effect
            mod._gateway = None
            gw = mod._get_gateway()
            profiles = gw.list_profiles()
            assert len(profiles) == 1
            assert profiles[0].model_profile_id == "my-profile"
            assert profiles[0].provider == "openai"
            assert profiles[0].model_name == "gpt-4o"
            assert profiles[0].enabled is True

    def test_env_defaults(self) -> None:
        mod = _import_module()
        from unittest.mock import patch as _patch

        with _patch.dict("os.environ", {}, clear=True):
            mod._gateway = None
            gw = mod._get_gateway()
            profiles = gw.list_profiles()
            assert len(profiles) == 1
            assert profiles[0].model_profile_id == "default"
