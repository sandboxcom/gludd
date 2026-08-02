from __future__ import annotations

from unittest.mock import AsyncMock, patch

from general_ludd.chat.daemon_runner import DaemonChatRunner


class TestDaemonChatRunnerInit:
    def test_default_construction(self) -> None:
        runner = DaemonChatRunner()
        assert runner._daemon_url == "http://localhost:8000"
        assert runner._model_profile_id == "default"
        assert len(runner.history) == 1
        assert runner.history[0]["role"] == "system"

    def test_custom_daemon_url(self) -> None:
        runner = DaemonChatRunner(daemon_url="http://example.com:9000")
        assert runner._daemon_url == "http://example.com:9000"

    def test_custom_model_profile(self) -> None:
        runner = DaemonChatRunner(model_profile_id="openai/gpt-4o")
        assert runner._model_profile_id == "openai/gpt-4o"

    def test_custom_system_prompt(self) -> None:
        runner = DaemonChatRunner(system_prompt="Be brief.")
        assert runner.history[0]["content"] == "Be brief."

    def test_eval_mode_set(self) -> None:
        runner = DaemonChatRunner(eval_mode=True)
        assert runner._eval_mode is True

    def test_eval_mode_defaults_false(self) -> None:
        runner = DaemonChatRunner()
        assert runner._eval_mode is False


class TestDaemonChatRunnerSendMessage:
    @patch("general_ludd.chat.daemon_runner.httpx.AsyncClient")
    async def test_send_message_success(self, mock_client_cls: AsyncMock) -> None:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = AsyncMock()
        mock_resp.json.return_value = {"response": "Hello!"}
        mock_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_resp

        runner = DaemonChatRunner()
        result = await runner.send_message("Hi")
        assert "Hello!" in result
        assert len(runner.history) == 3
        assert runner.history[1]["role"] == "user"
        assert runner.history[2]["role"] == "assistant"

    @patch("general_ludd.chat.daemon_runner.httpx.AsyncClient")
    async def test_send_message_empty_response(self, mock_client_cls: AsyncMock) -> None:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = AsyncMock()
        mock_resp.json.return_value = {"response": ""}
        mock_resp.raise_for_status.return_value = None
        mock_client.post.return_value = mock_resp

        runner = DaemonChatRunner()
        result = await runner.send_message("Hi")
        assert "empty response" in result.lower()

    @patch("general_ludd.chat.daemon_runner.httpx.AsyncClient")
    async def test_send_message_connect_error(self, mock_client_cls: AsyncMock) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("boom")

        runner = DaemonChatRunner()
        result = await runner.send_message("Hi")
        assert "could not connect" in result.lower()
        assert len(runner.history) == 1

    @patch("general_ludd.chat.daemon_runner.httpx.AsyncClient")
    async def test_send_message_http_error(self, mock_client_cls: AsyncMock) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_client.post.side_effect = httpx.HTTPStatusError("err", request=AsyncMock(), response=mock_resp)

        runner = DaemonChatRunner()
        result = await runner.send_message("Hi")
        assert "Daemon returned 500" in result


class TestDaemonChatRunnerClearHistory:
    def test_clear_history_keeps_system_prompt(self) -> None:
        runner = DaemonChatRunner(system_prompt="Custom prompt")
        runner.history.append({"role": "user", "content": "Hi"})
        runner.clear_history()
        assert len(runner.history) == 1
        assert runner.history[0]["role"] == "system"
        assert runner.history[0]["content"] == "Custom prompt"

    def test_clear_history_empty_uses_default(self) -> None:
        runner = DaemonChatRunner()
        runner.history = []
        runner.clear_history()
        assert len(runner.history) == 1
        assert runner.history[0]["role"] == "system"


class TestDaemonChatRunnerGetMessages:
    def test_get_messages_returns_copy(self) -> None:
        runner = DaemonChatRunner()
        msgs = runner.get_messages()
        assert len(msgs) == 1
        msgs.append({"role": "user", "content": "extra"})
        assert len(runner.history) == 1


class TestDaemonChatRunnerTruncateInput:
    def test_short_input_unchanged(self) -> None:
        result = DaemonChatRunner._truncate_input("hello")
        assert result == "hello"

    def test_long_input_truncated(self) -> None:
        long_text = "x" * 50_000
        result = DaemonChatRunner._truncate_input(long_text)
        assert len(result) == 32_000
