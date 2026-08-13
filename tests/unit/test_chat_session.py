from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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
            mock_response.json.return_value = {"choices": [{"message": {"content": ""}}]}
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
            assert f"Project directory: {Path(tmpdir).resolve()}" in content

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


class TestHistoryPersistence:
    def test_history_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = Path(tmpdir) / "test_session.jsonl"
            session = ChatSession(
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
            )
            session.history.append({"role": "user", "content": "hello"})
            session.history.append({"role": "assistant", "content": "hi there"})
            session.save_history()

            assert hist_path.exists()
            lines = hist_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) >= 3
            parsed = [json.loads(line) for line in lines]
            roles = [r["role"] for r in parsed]
            assert "system" in roles
            assert "user" in roles
            assert "assistant" in roles

            session2 = ChatSession(
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
            )
            assert len(session2.history) >= 3

    def test_history_file_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = Path(tmpdir) / "test_format.jsonl"
            session = ChatSession(
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
            )
            session.history.append({"role": "user", "content": "test message"})
            session.history.append({"role": "assistant", "content": "response"})
            session.save_history()

            with hist_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    assert "role" in record
                    assert "content" in record
                    assert "timestamp" in record
                    assert record["role"] in ("system", "user", "assistant")

    def test_auto_save_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = Path(tmpdir) / "auto_save.jsonl"
            session = ChatSession(
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
                save_interval=2,
            )
            session.history.append({"role": "user", "content": "turn 1"})
            session.history.append({"role": "assistant", "content": "resp 1"})
            session._maybe_auto_save()

            assert not hist_path.exists()

            session.history.append({"role": "user", "content": "turn 2"})
            session.history.append({"role": "assistant", "content": "resp 2"})
            session._maybe_auto_save()

            assert hist_path.exists()

    def test_list_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.chat.session import ChatSession as CS

            hist_dir = Path(tmpdir)
            index_path = hist_dir / "index.json"
            index_data = {
                "sessions": [
                    {
                        "file": str(hist_dir / "session_20260101_120000.jsonl"),
                        "timestamp": "2026-01-01T12:00:00+00:00",
                        "model": "openai/gpt-4o",
                        "message_count": 6,
                        "preview": "hello world",
                    },
                    {
                        "file": str(hist_dir / "session_20260102_130000.jsonl"),
                        "timestamp": "2026-01-02T13:00:00+00:00",
                        "model": "deepseek/deepseek-chat",
                        "message_count": 10,
                        "preview": "write a function",
                    },
                ]
            }
            hist_dir.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(index_data), encoding="utf-8")

            sessions = CS.list_sessions(history_dir=hist_dir)
            assert len(sessions) == 2
            assert sessions[0]["model"] == "openai/gpt-4o"
            assert sessions[1]["message_count"] == 10
            assert sessions[0]["preview"] == "hello world"

    def test_list_sessions_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.chat.session import ChatSession as CS

            hist_dir = Path(tmpdir)
            sessions = CS.list_sessions(history_dir=hist_dir)
            assert sessions == []

    def test_resume_last_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.chat.session import ChatSession as CS

            hist_file = Path(tmpdir) / "session_resume.jsonl"
            messages = "\n".join(
                [
                    json.dumps({"role": "system", "content": "custom prompt"}),
                    json.dumps({"role": "user", "content": "previous question"}),
                    json.dumps({"role": "assistant", "content": "previous answer"}),
                ]
            )
            hist_file.write_text(messages + "\n", encoding="utf-8")

            index_path = Path(tmpdir) / "index.json"
            index_data = {
                "sessions": [
                    {
                        "file": str(hist_file),
                        "timestamp": "2026-01-01T12:00:00+00:00",
                        "model": "openai/gpt-4o",
                        "message_count": 3,
                        "preview": "previous question",
                    }
                ]
            }
            index_path.write_text(json.dumps(index_data), encoding="utf-8")

            with patch("general_ludd.chat.session.DEFAULT_HISTORY_DIR", Path(tmpdir)):
                session = CS(
                    api_base_url="https://test.api/v1",
                    api_key="sk-test",
                    resume=True,
                )
                assert len(session.history) == 3
                assert session.history[0]["content"] == "custom prompt"
                assert session.history[1]["role"] == "user"
                assert session.history[1]["content"] == "previous question"

    def test_resume_no_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.chat.session import ChatSession as CS

            with patch("general_ludd.chat.session.DEFAULT_HISTORY_DIR", Path(tmpdir)):
                session = CS(
                    api_base_url="https://test.api/v1",
                    api_key="sk-test",
                    resume=True,
                )
                assert len(session.history) == 1
                assert session.history[0]["role"] == "system"

    def test_save_on_exit_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = Path(tmpdir) / "save_exit.jsonl"
            session = ChatSession(
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
            )
            session.history.append({"role": "user", "content": "hello"})
            session.history.append({"role": "assistant", "content": "hi"})
            session.save_history()

            assert hist_path.exists()
            content = hist_path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in content.strip().split("\n")]
            roles = [r["role"] for r in records]
            assert "user" in roles
            assert "assistant" in roles

    def test_session_metadata_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = Path(tmpdir) / "metadata_test.jsonl"
            session = ChatSession(
                model="deepseek/deepseek-chat",
                api_base_url="https://test.api/v1",
                api_key="sk-test",
                history_file=str(hist_path),
            )
            session._history_dir = Path(tmpdir)
            session.history.append({"role": "user", "content": "test prompt"})
            session.history.append({"role": "assistant", "content": "test response"})
            session.save_history()

            from general_ludd.chat.session import ChatSession as CS

            sessions = CS.list_sessions(history_dir=Path(tmpdir))
            assert len(sessions) >= 1
            entry = sessions[0]
            assert "model" in entry
            assert "message_count" in entry
            assert "preview" in entry
            assert "timestamp" in entry
            assert "file" in entry


class TestRunOnceSuccess:
    @pytest.mark.asyncio
    async def test_run_once_successful_response(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
            system_prompt="Be concise.",
        )
        response_json = {"choices": [{"message": {"content": "The answer is 42."}}]}
        mock_response = Mock()
        mock_response.json.return_value = response_json

        with patch.object(session, "_post_with_retry") as mock_post:
            mock_post.return_value = mock_response
            result = await session.run_once("What is the answer?")

        assert "The answer is 42." in result
        assert len(session.history) == 3
        assert session.history[1]["role"] == "user"
        assert session.history[1]["content"] == "What is the answer?"
        assert session.history[2]["role"] == "assistant"
        assert "The answer is 42." in session.history[2]["content"]

    @pytest.mark.asyncio
    async def test_run_once_preserves_system_prompt(self) -> None:
        session = ChatSession(
            model="openai/gpt-4o",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
            system_prompt="Custom system prompt.",
        )
        response_json = {"choices": [{"message": {"content": "OK"}}]}
        mock_response = Mock()
        mock_response.json.return_value = response_json

        with patch.object(session, "_post_with_retry") as mock_post:
            mock_post.return_value = mock_response
            await session.run_once("hi")

        assert session.history[0]["role"] == "system"
        assert session.history[0]["content"] == "Custom system prompt."

    @pytest.mark.asyncio
    async def test_run_once_formats_code_blocks(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        code_response = "```python\ndef foo():\n    return 42\n```"
        response_json = {"choices": [{"message": {"content": code_response}}]}
        mock_response = Mock()
        mock_response.json.return_value = response_json

        with patch.object(session, "_post_with_retry") as mock_post:
            mock_post.return_value = mock_response
            result = await session.run_once("write a function")

        assert "def" in result

    @pytest.mark.asyncio
    async def test_run_once_http_error_raises(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        import httpx

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Gateway",
            request=httpx.Request("POST", "https://test.api/v1"),
            response=httpx.Response(502),
        )

        with patch.object(session, "_post_with_retry") as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "Bad Gateway",
                request=httpx.Request("POST", "https://test.api/v1"),
                response=httpx.Response(502),
            )
            result = await session.run_once("test")
            assert "Error" in result


class _AsyncIter:
    """Real async iterator for mocking httpx aiter_lines()."""

    def __init__(self, items: list[str]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


class TestStreamResponseSuccess:
    @pytest.mark.asyncio
    async def test_stream_response_chunks(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        chunk1 = 'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
        chunk2 = 'data: {"choices":[{"delta":{"content":" there"}}]}\n'
        chunk3 = "data: [DONE]\n"

        mock_aiter_lines = Mock(return_value=_AsyncIter([chunk1, chunk2, chunk3]))

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.aiter_lines = mock_aiter_lines
        mock_stream.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_stream
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await session.stream_response("say hi")

        assert "Hello there" in result
        assert len(session.history) == 3
        assert session.history[2]["role"] == "assistant"
        assert "Hello there" in session.history[2]["content"]

    @pytest.mark.asyncio
    async def test_stream_response_empty(self) -> None:
        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )
        chunk = "data: [DONE]\n"

        mock_aiter_lines = Mock(return_value=_AsyncIter([chunk]))

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.aiter_lines = mock_aiter_lines
        mock_stream.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_stream
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await session.stream_response("test")

        assert "empty response" in result.lower()
