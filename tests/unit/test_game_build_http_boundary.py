"""Deep tests for the collection's stdlib-only model HTTP boundary."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ansible_collections.general_ludd.agent.plugins.module_utils import gludd
from ansible_collections.general_ludd.agent.plugins.modules import game_build


class _Response:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_result_and_structured_output_helpers() -> None:
    assert gludd.ok_result({"value": 1}, changed=True) == {
        "failed": False,
        "changed": True,
        "value": 1,
    }
    assert gludd.error_result("bad", status=7) == {
        "failed": True,
        "changed": False,
        "msg": "bad",
        "status": 7,
    }
    assert gludd.strip_code_fences("```json\n{\"ok\": true}\n```") == '{"ok": true}'
    assert gludd.strip_code_fences(" plain ") == "plain"
    assert gludd.parse_structured('{"ok": true}') == ({"ok": True}, None)
    assert gludd.parse_structured("")[0] is None
    assert gludd.parse_structured(None)[1] == "empty model output (None)"
    assert "not valid JSON" in str(gludd.parse_structured("not-json")[1])


def test_client_builds_urls_headers_and_query() -> None:
    client = gludd.GluddClient("http://127.0.0.1:8000/", psk="key", timeout=9)
    assert client._url("/healthz") == "http://127.0.0.1:8000/healthz"
    assert client._headers()["X-PSK"] == "key"
    with patch.object(client, "_send", return_value={"_status": 200}) as send:
        client.get("/items", {"a": "b c"})
        client.post("/items", {"x": 1})
        client.patch("/items/1", {"x": 2})
    requests = [call.args[0] for call in send.call_args_list]
    assert requests[0].full_url.endswith("/items?a=b+c")
    assert [request.method for request in requests] == ["GET", "POST", "PATCH"]


def test_send_handles_json_raw_http_and_url_errors() -> None:
    client = gludd.GluddClient(psk="key")
    request = MagicMock()
    with patch("urllib.request.urlopen", return_value=_Response(b'{"value": 1}', 201)):
        assert client._send(request) == {"value": 1, "_status": 201}
    with patch("urllib.request.urlopen", return_value=_Response(b"plain", 200)):
        assert client._send(request) == {"_raw": "plain", "_status": 200}

    http_error = urllib.error.HTTPError(
        url="http://localhost",
        code=403,
        msg="forbidden",
        hdrs=Message(),
        fp=io.BytesIO(b'{"detail": "denied"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        assert client._send(request) == {"detail": "denied", "_status": 403}
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert client._send(request)["_error"] == "offline"
    with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
        assert client._send(request)["_error"] == "boom"


def test_reachable_and_model_payload_contract() -> None:
    client = gludd.GluddClient(psk="key")
    with patch.object(client, "get", return_value={"_status": 200}):
        assert client.reachable() is True
    with patch.object(client, "get", return_value={"_status": "200"}):
        assert client.reachable() is False
    with patch.object(client, "get", side_effect=RuntimeError("offline")):
        assert client.reachable() is False

    with patch.object(client, "post", return_value={"_status": 200}) as post:
        client.call_model("prompt", model_profile="small", route_task_type="game", max_tokens=33)
    assert post.call_args.args == (
        "/admin/models/call",
        {"prompt": "prompt", "max_tokens": 33, "model_profile": "small", "task_type": "game"},
    )
    assert gludd.GluddClient(psk="").call_model("prompt")["failed"] is True


def _module(check_mode: bool, *, psk: str = "key") -> MagicMock:
    module = MagicMock()
    module.check_mode = check_mode
    module.params = {
        "prompt": "build snake",
        "model_profile": "local.small",
        "temperature": 0.0,
        "daemon_url": "http://127.0.0.1:8000",
        "psk": psk,
        "timeout": 30,
    }
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    return module


def test_game_build_check_mode_is_side_effect_free() -> None:
    module = _module(True)
    with patch.object(game_build, "AnsibleModule", return_value=module), pytest.raises(SystemExit, match="0"):
        game_build.main()
    module.exit_json.assert_called_once()
    assert module.exit_json.call_args.kwargs["transport_used"] == "none"


def test_game_build_success_uses_one_http_client() -> None:
    module = _module(False)
    client = MagicMock()
    client.call_model.return_value = {"_status": 200, "text": "code"}
    factory = MagicMock(return_value=client)
    with (
        patch.object(game_build, "AnsibleModule", return_value=module),
        patch.object(game_build, "GluddClient", factory),
        pytest.raises(SystemExit, match="0"),
    ):
        game_build.main()
    factory.assert_called_once()
    client.call_model.assert_called_once_with("build snake", model_profile="local.small", max_tokens=4096)
    assert module.exit_json.call_args.kwargs["transport_used"] == "http"


@pytest.mark.parametrize(
    "response",
    [
        {"_error": "offline", "_status": 0},
        {"_status": 503, "detail": "warming"},
    ],
)
def test_game_build_fails_closed_on_transport_errors(response: dict[str, object]) -> None:
    module = _module(False)
    client = SimpleNamespace(call_model=MagicMock(return_value=response))
    with (
        patch.object(game_build, "AnsibleModule", return_value=module),
        patch.object(game_build, "GluddClient", return_value=client),
        pytest.raises(SystemExit, match="1"),
    ):
        game_build.main()
    module.fail_json.assert_called_once()
