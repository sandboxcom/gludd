"""Branch contracts for beta4 approval, browser login, and daemon chat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)
from general_ludd.auth.browser_login import (
    BrowserLoginFlow,
    EnvCredentialStore,
    OpenBaoCredentialStore,
    ServiceConfig,
    _open_browser,
)
from general_ludd.chat.daemon_runner import MAX_INPUT_LENGTH, DaemonChatRunner
from general_ludd.db.repository import HumanTodoRepository
from general_ludd.security.url_fetch import FetchResult


class _ApprovalRepo:
    def __init__(self, row: object | None = None) -> None:
        self.row = row
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.created.append(kwargs)
        return object()

    async def get(self, _todo_id: str) -> object | None:
        return self.row


@pytest.mark.asyncio
async def test_approval_request_schedules_repository_write() -> None:
    repo = _ApprovalRepo()
    gate = ApprovalGate(lambda: cast(HumanTodoRepository, repo))
    request = ApprovalRequest(target="cluster", action="destroy", by="operator")

    response = gate.request_approval(request)
    await asyncio.sleep(0)

    assert response.decision is ApprovalDecision.PENDING
    assert request.resource_id == "cluster"
    assert request.requester == "operator"
    assert repo.created == [
        {
            "agent_id": "operator",
            "title": "Approval: destroy on cluster",
            "body": "No reason provided",
            "category": "permission_escalation",
            "priority": "high",
        }
    ]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, ApprovalDecision.PENDING),
        (SimpleNamespace(status="done"), ApprovalDecision.APPROVED),
        (SimpleNamespace(status="dismissed"), ApprovalDecision.DENIED),
        (SimpleNamespace(status="superseded"), ApprovalDecision.DENIED),
        (SimpleNamespace(status="open"), ApprovalDecision.PENDING),
        (SimpleNamespace(), ApprovalDecision.PENDING),
    ],
)
def test_approval_decision_maps_repository_status(
    row: object | None,
    expected: ApprovalDecision,
) -> None:
    repo = _ApprovalRepo(row)
    gate = ApprovalGate(lambda: cast(HumanTodoRepository, repo))
    assert gate.check_decision("ht-1") is expected


def test_approval_fail_closed_on_missing_or_failing_repository() -> None:
    assert ApprovalGate(lambda: None).check_decision("missing") is ApprovalDecision.PENDING
    assert ApprovalGate(lambda: None).check(ApprovalRequest()).allowed is False
    gate = ApprovalGate(lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert gate.request_approval(ApprovalRequest()).decision is ApprovalDecision.PENDING
    assert gate.check_decision("broken") is ApprovalDecision.PENDING


def test_approval_running_loop_never_blocks() -> None:
    async def _exercise() -> ApprovalDecision:
        repo = _ApprovalRepo()
        return ApprovalGate(lambda: cast(HumanTodoRepository, repo)).check_decision("ht-1")

    assert asyncio.run(_exercise()) is ApprovalDecision.PENDING


class _Secrets:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.fail_reads = False

    def write_secret(self, path: str, payload: dict[str, Any]) -> None:
        self.data[path] = payload

    def read_secret(self, path: str) -> dict[str, Any] | None:
        if self.fail_reads:
            raise RuntimeError("sealed")
        return self.data.get(path)


def test_openbao_store_round_trip_metadata_and_fail_closed() -> None:
    secrets = _Secrets()
    store = OpenBaoCredentialStore(secrets)  # type: ignore[arg-type]
    store.store("github", "token", {"scope": "repo"})
    assert store.retrieve("github") == "token"
    store.store_metadata("github", {"scope": "workflow"})
    assert json.loads(secrets.data["gludd/auth/github"]["metadata"])["scope"] == "workflow"
    secrets.fail_reads = True
    assert store.retrieve("github") is None
    store.store_metadata("github", {"ignored": True})


def test_env_store_reads_file_and_rewrites_single_key(tmp_path: Path) -> None:
    env_file = tmp_path / "credentials.env"
    env_file.write_text('export OPENAI_API_KEY="old"\n\nexport OTHER="kept"\n')
    store = EnvCredentialStore(env_file)
    with patch.dict("os.environ", {}, clear=True):
        assert store.retrieve("openai") == "old"
        store.store("openai", "new")
    text = env_file.read_text()
    assert text.count("export OPENAI_API_KEY=") == 1
    assert 'export OTHER="kept"' in text


def test_browser_launcher_falls_back_to_webbrowser() -> None:
    with (
        patch("general_ludd.auth.browser_login.subprocess.Popen", side_effect=OSError("missing")),
        patch("general_ludd.auth.browser_login.webbrowser.open") as fallback,
    ):
        assert _open_browser("https://example.test") is None
    fallback.assert_called_once_with("https://example.test")


def _oauth_config() -> ServiceConfig:
    return ServiceConfig(
        name="test",
        display_name="Test",
        auth_url="https://auth.example.test/login",
        exchange_url="https://token.example.test/exchange",
        client_id_env="TEST_CLIENT_ID",
        client_credential_env="TEST_CLIENT_SECRET",
        credential_env="TEST_TOKEN",
    )


@pytest.mark.parametrize("body", [{"token": "ok"}, {"error_description": "denied"}, {"error": "bad"}])
def test_oauth_exchange_token_alias_and_errors(body: dict[str, str]) -> None:
    store = MagicMock()
    flow = BrowserLoginFlow.from_config(_oauth_config(), store=store)
    response = FetchResult(
        url="https://token.example.test/exchange",
        status_code=200,
        headers={},
        content=json.dumps(body).encode(),
    )
    with patch("general_ludd.auth.browser_login.secure_fetch", return_value=response):
        result = flow._exchange_code("code", "verifier", "http://callback", "id", "secret")
    if "token" in body:
        assert result == "ok"
        store.store.assert_called_once()
    else:
        assert result is None
        store.store.assert_not_called()


def test_oauth_exchange_fails_closed_on_invalid_response() -> None:
    flow = BrowserLoginFlow.from_config(_oauth_config(), store=MagicMock())
    with patch("general_ludd.auth.browser_login.secure_fetch", side_effect=ValueError("invalid")):
        assert flow._exchange_code("code", "verifier", "http://callback", "id", "") is None


def test_oauth_callback_denial_and_state_mismatch_close_server() -> None:
    store = MagicMock()
    flow = BrowserLoginFlow.from_config(_oauth_config(), store=store)
    server = MagicMock()
    with (
        patch.dict("os.environ", {"TEST_CLIENT_ID": "id"}, clear=True),
        patch("general_ludd.auth.browser_login._start_callback_server", return_value=server),
        patch("general_ludd.auth.browser_login._open_browser"),
        patch("general_ludd.auth.browser_login._CallbackHandler.done.wait", return_value=True),
        patch("general_ludd.auth.browser_login._CallbackHandler.captured_code", "code"),
        patch("general_ludd.auth.browser_login._CallbackHandler.captured_state", "wrong"),
    ):
        assert flow.run(timeout=1) is None
    server.shutdown.assert_called_once()
    server.server_close.assert_called_once()


def test_api_key_cancel_and_payment_metadata_fail_closed() -> None:
    store = MagicMock()
    store.retrieve.return_value = None
    store._sm = None
    flow = BrowserLoginFlow("openai", store=store, payment_label="card")
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("general_ludd.auth.browser_login._open_browser"),
        patch("builtins.input", side_effect=EOFError),
    ):
        assert flow.run(timeout=1) is None
    assert flow._payment_metadata() == {}


@pytest.mark.parametrize(
    ("token", "last4", "expected"),
    [
        (None, None, {}),
        ("processor-token", None, {"payment_processor_token": "processor-token"}),
        (
            "processor-token",
            "4242",
            {"payment_processor_token": "processor-token", "payment_card_last4": "4242"},
        ),
    ],
)
def test_payment_metadata_is_minimal_and_tokenized(
    token: str | None,
    last4: str | None,
    expected: dict[str, str],
) -> None:
    store = MagicMock()
    store._sm = object()
    flow = BrowserLoginFlow("openai", store=store, payment_label="card")
    vault = MagicMock()
    vault.get_processor_token.return_value = token
    vault.get_card_last4.return_value = last4
    with patch("general_ludd.secrets.payment_vault.SecurePaymentVault", return_value=vault):
        assert flow._payment_metadata() == expected


class _StreamResponse:
    def __init__(self, lines: list[str], error: Exception | None = None) -> None:
        self.lines = lines
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self.lines:
            yield line


class _StreamContext:
    def __init__(self, response: _StreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _StreamResponse:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _ChatClient:
    def __init__(self, response: _StreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _ChatClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object) -> _StreamContext:
        return _StreamContext(self.response)


class _PromptSession:
    effects: ClassVar[list[str | BaseException]] = []

    @classmethod
    def __class_getitem__(cls, _item: object) -> type[_PromptSession]:
        return cls

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def prompt_async(self, _prompt: str) -> str:
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


@pytest.mark.asyncio
async def test_stream_message_handles_chunks_done_and_empty() -> None:
    response = _StreamResponse(["", "event: ignored", 'data: {"chunk": 1}', "data: [DONE]"])
    with patch("general_ludd.chat.daemon_runner.httpx.AsyncClient", return_value=_ChatClient(response)):
        runner = DaemonChatRunner()
        result = await runner.stream_message("hello")
    assert result == '{"chunk": 1}'
    assert runner.history[-1] == {"role": "assistant", "content": result}

    empty = _StreamResponse(["data: [DONE]"])
    with patch("general_ludd.chat.daemon_runner.httpx.AsyncClient", return_value=_ChatClient(empty)):
        result = await DaemonChatRunner().stream_message("hello")
    assert "empty response" in result.lower()


@pytest.mark.asyncio
async def test_stream_message_daemon_error_and_transport_failure() -> None:
    error_response = _StreamResponse(['data: {"error": "denied"}'])
    with patch("general_ludd.chat.daemon_runner.httpx.AsyncClient", return_value=_ChatClient(error_response)):
        runner = DaemonChatRunner()
        assert await runner.stream_message("hello") == ""
        assert len(runner.history) == 1

    request = httpx.Request("POST", "http://daemon")
    transport_error = _StreamResponse([], httpx.ConnectError("offline", request=request))
    with patch("general_ludd.chat.daemon_runner.httpx.AsyncClient", return_value=_ChatClient(transport_error)):
        runner = DaemonChatRunner()
        assert await runner.stream_message("hello") == ""
        assert len(runner.history) == 1


@pytest.mark.asyncio
async def test_send_message_unexpected_failure_and_eval_routes() -> None:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = RuntimeError("broken")
    with patch("general_ludd.chat.daemon_runner.httpx.AsyncClient", return_value=client):
        runner = DaemonChatRunner()
        assert "broken" in await runner.send_message("x")
        assert len(runner.history) == 1

    runner = DaemonChatRunner()
    runner.send_message = AsyncMock(return_value="sync")  # type: ignore[method-assign]
    runner.stream_message = AsyncMock(return_value="stream")  # type: ignore[method-assign]
    assert await runner.run_eval("x") == "sync"
    assert await runner.run_eval("x", stream=True) == "stream"


@pytest.mark.asyncio
async def test_repl_quit_blank_truncate_and_stream_failure() -> None:
    _PromptSession.effects = [" ", "x" * (MAX_INPUT_LENGTH + 1), "/quit"]
    runner = DaemonChatRunner()
    runner.stream_message = AsyncMock(side_effect=[RuntimeError("boom"), "ok"])  # type: ignore[method-assign]
    with patch("prompt_toolkit.PromptSession", _PromptSession):
        await runner.start_repl()
    runner.stream_message.assert_awaited_once()
    call = runner.stream_message.await_args
    assert call is not None
    prompt = call.args[0]
    assert len(prompt) == MAX_INPUT_LENGTH


@pytest.mark.asyncio
async def test_repl_keyboard_interrupt_and_eof_paths() -> None:
    _PromptSession.effects = [KeyboardInterrupt(), KeyboardInterrupt()]
    with patch("prompt_toolkit.PromptSession", _PromptSession):
        await DaemonChatRunner().start_repl()

    _PromptSession.effects = [EOFError()]
    with patch("prompt_toolkit.PromptSession", _PromptSession):
        await DaemonChatRunner().start_repl()
