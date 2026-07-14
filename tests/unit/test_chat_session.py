from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

from general_ludd.chat import ChatSession


class TestChatSessionInit:
    def test_default_construction(self) -> None:
        session = ChatSession()
        assert session._model_arg == "default"
        assert session.eval_mode is False
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"
        assert "ansible" in session.history[0]["content"].lower()
        assert session._provider == "openai"
        assert session._model_id == "gpt-4o"

    def test_provider_model_parsing(self) -> None:
        session = ChatSession(model="deepseek/deepseek-chat")
        assert session._provider == "deepseek"
        assert session._model_id == "deepseek-chat"

    def test_custom_system_prompt(self) -> None:
        session = ChatSession(system_prompt="Be concise.")
        assert session.history[0]["content"] == "Be concise."

    def test_eval_mode_set(self) -> None:
        session = ChatSession(eval_mode=True)
        assert session.eval_mode is True

    def test_eval_mode_defaults_false(self) -> None:
        session = ChatSession()
        assert session.eval_mode is False

    def test_default_model_uses_openai(self) -> None:
        session = ChatSession(model="default")
        assert session._provider == "openai"

    def test_bare_provider_name_resolves_flagship(self) -> None:
        session = ChatSession(model="deepseek")
        assert session._provider == "deepseek"
        assert session._model_id == "deepseek-chat"

    def test_bare_unknown_name_treated_as_model(self) -> None:
        session = ChatSession(model="unknown-model")
        assert session._provider == "openai"
        assert session._model_id == "unknown-model"

    def test_provider_slash_empty_uses_flagship(self) -> None:
        session = ChatSession(model="deepseek/")
        assert session._provider == "deepseek"
        assert session._model_id == "deepseek-chat"

    def test_api_base_and_key_overrides_stored(self) -> None:
        session = ChatSession(api_base_url="https://custom.api/v1", api_key="sk-test")
        assert session._api_base_override == "https://custom.api/v1"
        assert session._api_key_override == "sk-test"

    def test_project_dir_stored(self) -> None:
        session = ChatSession(project_dir="/tmp/fake-project")
        assert session._project_dir == "/tmp/fake-project"


class TestChatSessionResolveModel:
    def test_provider_default_model_mapping(self) -> None:
        session = ChatSession(model="deepseek/deepseek-chat")
        assert session._model_id == "deepseek-chat"

    def test_unknown_provider_no_default(self) -> None:
        session = ChatSession(model="custom/some-model")
        assert session._provider == "custom"
        assert session._model_id == "some-model"

    def test_no_slash_means_default(self) -> None:
        session = ChatSession(model="gpt-4o")
        assert session._provider == "openai"


class TestChatSessionResolveApiConfig:
    def test_full_overrides_skip_preset(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://custom.api/v1",
            api_key="sk-test",
        )
        base_url, api_key = session._resolve_api_config()
        assert base_url == "https://custom.api/v1"
        assert api_key == "sk-test"

    def test_partial_overrides_merge_with_preset(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://custom.api/v1",
        )
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}):
            base_url, api_key = session._resolve_api_config()
            assert base_url == "https://custom.api/v1"
            assert api_key == "sk-env"

    def test_key_only_override_uses_preset_base(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_key="sk-direct",
        )
        base_url, api_key = session._resolve_api_config()
        assert base_url == "https://api.deepseek.com/v1"
        assert api_key == "sk-direct"

    def test_missing_key_raises_runtime_error(self) -> None:
        session = ChatSession(model="deepseek/deepseek-chat")
        with patch.dict(os.environ, {}, clear=True):
            try:
                session._resolve_api_config()
            except RuntimeError as e:
                assert "DEEPSEEK_API_KEY" in str(e)

    def test_unknown_provider_raises_value_error(self) -> None:
        session = ChatSession(model="fake/nonexistent")
        with patch.dict(os.environ, {}, clear=True):
            try:
                session._resolve_api_config()
            except ValueError as e:
                assert "fake" in str(e)


class TestChatSessionBuildEndpoint:
    def test_chat_completions_present(self) -> None:
        url = ChatSession._build_endpoint("https://api.openai.com/v1/chat/completions")
        assert url == "https://api.openai.com/v1/chat/completions"

    def test_base_url_appends(self) -> None:
        url = ChatSession._build_endpoint("https://api.openai.com/v1")
        assert url == "https://api.openai.com/v1/chat/completions"

    def test_trailing_slash(self) -> None:
        url = ChatSession._build_endpoint("https://api.openai.com/v1/")
        assert url == "https://api.openai.com/v1/chat/completions"


class TestTruncateInput:
    def test_input_within_limit(self) -> None:
        result = ChatSession._truncate_input("hello")
        assert result == "hello"

    def test_input_exceeds_limit(self) -> None:
        from general_ludd.chat.session import MAX_INPUT_LENGTH
        long_input = "x" * (MAX_INPUT_LENGTH + 100)
        result = ChatSession._truncate_input(long_input)
        assert len(result) == MAX_INPUT_LENGTH

    def test_input_at_limit(self) -> None:
        from general_ludd.chat.session import MAX_INPUT_LENGTH
        exact = "x" * MAX_INPUT_LENGTH
        result = ChatSession._truncate_input(exact)
        assert len(result) == MAX_INPUT_LENGTH


class TestEmptyModelResponse:
    @pytest.mark.asyncio
    async def test_run_once_empty_response(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        with patch.object(session, "_post_with_retry") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": ""}}]
            }
            mock_post.return_value = mock_response
            result = await session.run_once("hello")
            assert "empty response" in result.lower()

    @pytest.mark.asyncio
    async def test_run_once_missing_content_key(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        with patch.object(session, "_post_with_retry") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"choices": []}
            mock_post.return_value = mock_response
            result = await session.run_once("hello")
            assert "empty response" in result.lower()


class TestConnectionErrors:
    @pytest.mark.asyncio
    async def test_run_once_connect_error(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        with patch.object(httpx.AsyncClient, "post", side_effect=httpx.ConnectError("refused")):
            result = await session.run_once("hello")
            assert "could not connect" in result.lower()

    @pytest.mark.asyncio
    async def test_run_once_timeout(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        with patch.object(httpx.AsyncClient, "post", side_effect=httpx.TimeoutException("timeout")):
            result = await session.run_once("hello")
            assert "timed out" in result.lower()


class TestContextInjection:
    def test_no_project_dir_no_context(self) -> None:
        session = ChatSession()
        content = session.history[0]["content"]
        assert "Project directory:" not in content
        assert "Ansible Inventory" not in content
        assert "Terraform State" not in content

    def test_project_dir_in_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert f"Project directory: {tmpdir}" in content

    def test_nonexistent_project_dir_no_context(self) -> None:
        session = ChatSession(project_dir="/tmp/does-not-exist-12345")
        content = session.history[0]["content"]
        assert "Project directory:" not in content
        assert "ansible" in content.lower()

    def test_ansible_inventory_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_path = Path(tmpdir) / "inventory.yml"
            inv_path.write_text("[web]\nwebserver1 ansible_host=192.168.1.10\n")
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Ansible Inventory" in content
            assert "[web]" in content
            assert "webserver1" in content

    def test_ansible_inventory_yaml_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_path = Path(tmpdir) / "inventory.yaml"
            inv_path.write_text("all:\n  hosts:\n    server1:\n")
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Ansible Inventory" in content
            assert "server1" in content

    def test_ansible_hosts_file_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ansible_dir = Path(tmpdir) / "ansible"
            ansible_dir.mkdir()
            (ansible_dir / "hosts").write_text("[db]\ndb1 ansible_host=10.0.0.1\n")
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Ansible Inventory" in content
            assert "[db]" in content

    def test_terraform_tfstate_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_path = Path(tmpdir) / "terraform.tfstate"
            tf_path.write_text('{"version": 1, "outputs": {"vpc_id": {"value": "vpc-123"}}}')
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Terraform State" in content
            assert "vpc-123" in content

    def test_terraform_subdir_tfstate_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = Path(tmpdir) / "terraform"
            tf_dir.mkdir()
            (tf_dir / "terraform.tfstate").write_text('{"version": 1}')
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Terraform State" in content

    def test_both_ansible_and_terraform_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "inventory").write_text("[all]\n")
            (Path(tmpdir) / "terraform.tfstate").write_text('{"version": 1}')
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Ansible Inventory" in content
            assert "Terraform State" in content
            assert "Project directory:" in content

    def test_custom_system_prompt_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "inventory").write_text("[all]\n")
            session = ChatSession(
                project_dir=tmpdir,
                system_prompt="Be concise.",
            )
            content = session.history[0]["content"]
            assert content.startswith("Be concise.")
            assert "Ansible Inventory" in content

    def test_large_inventory_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_path = Path(tmpdir) / "inventory"
            large_content = "[all]\n" + "\n".join(f"host{i}" for i in range(2000))
            inv_path.write_text(large_content)
            session = ChatSession(project_dir=tmpdir)
            content = session.history[0]["content"]
            assert "Ansible Inventory" in content
            assert "[truncated]" in content
