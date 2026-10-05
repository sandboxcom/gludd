"""Compatibility coverage for the isolated agent collection clients."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

import pytest
from ansible_collections.general_ludd.agent.plugins.module_utils import (
    embeddings,
    gludd,
    searxng,
)


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _raising(exc: Exception) -> Callable[..., Any]:
    def raise_error(*_args: object, **_kwargs: object) -> Any:
        raise exc

    return raise_error


def test_result_and_structured_output_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gludd.ok_result({"value": 1}, changed=True) == {
        "failed": False,
        "changed": True,
        "value": 1,
    }
    assert gludd.error_result("bad", detail="why")["detail"] == "why"
    assert gludd.strip_code_fences("```json\n{\"ok\": true}\n```") == '{"ok": true}'
    assert gludd.strip_code_fences(" plain ") == "plain"
    assert gludd.strip_code_fences(3) == 3
    assert gludd.parse_structured(None)[1] == "empty model output (None)"
    assert gludd.parse_structured("   ")[1] == "empty model output after fence strip"
    assert gludd.parse_structured("not-json")[1].startswith("not valid JSON:")
    assert gludd.parse_structured("{\"ok\": true}") == ({"ok": True}, None)

    monkeypatch.setattr(gludd, "strip_code_fences", _raising(RuntimeError("broken")))
    assert gludd.parse_structured("{}") == (None, "fence-strip failed: broken")


def test_client_builds_authenticated_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gludd.GluddClient("http://daemon/", psk="secret", timeout=7)
    seen: list[urllib.request.Request] = []

    def send(request: urllib.request.Request) -> dict[str, Any]:
        seen.append(request)
        return {"_status": 201, "value": "ok"}

    monkeypatch.setattr(client, "_send", send)
    assert client.get("/items", {"q": "a b"})["value"] == "ok"
    assert client._get("/items") == (201, {"value": "ok"})
    assert client.post("items", {"x": 1})["_status"] == 201
    assert client.patch("items", {"x": 2})["_status"] == 201
    assert client.delete("items")["_status"] == 201
    assert client.delete("items", {"x": 3})["_status"] == 201

    assert seen[0].full_url == "http://daemon/items?q=a+b"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert seen[0].headers["X-psk"] == "secret"
    assert [request.method for request in seen[2:]] == ["POST", "PATCH", "DELETE", "DELETE"]
    assert seen[-1].data == json.dumps({"x": 3}).encode()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_Response(b'{"ok": true}', 202), {"ok": True, "_status": 202}),
        (_Response(b"not-json", 200), {"_raw": "not-json", "_status": 200}),
    ],
)
def test_client_send_decodes_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: _Response,
    expected: dict[str, Any],
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    assert gludd.GluddClient().get("healthz") == expected


def test_client_send_maps_http_and_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    http_error = urllib.error.HTTPError(
        "http://daemon/items",
        503,
        "down",
        Message(),
        io.BytesIO(b'{"detail": "down"}'),
    )
    monkeypatch.setattr(urllib.request, "urlopen", _raising(http_error))
    assert gludd.GluddClient().get("items") == {"detail": "down", "_status": 503}

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _raising(urllib.error.URLError("refused")),
    )
    refused = gludd.GluddClient().get("items")
    assert refused == {"_error": "refused", "_status": 0, "_raw": ""}

    monkeypatch.setattr(urllib.request, "urlopen", _raising(RuntimeError("boom")))
    assert gludd.GluddClient().get("items")["_error"] == "boom"


def test_client_health_reachability_and_model_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gludd.GluddClient(psk="secret")
    monkeypatch.setattr(client, "get", lambda *_args, **_kwargs: {"_status": 200})
    assert client.reachable() is True
    assert client.health() == {"ok": True, "status": 200, "body": {}}

    monkeypatch.setattr(client, "get", lambda *_args, **_kwargs: {"_status": "bad"})
    assert client.reachable() is False
    assert client.health() == {"ok": False, "status": 0, "detail": "HTTP 0"}

    monkeypatch.setattr(client, "get", _raising(RuntimeError("offline")))
    assert client.reachable() is False

    posted: dict[str, Any] = {}

    def post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        posted.update({"path": path, "body": body})
        return {"_status": 200}

    monkeypatch.setattr(client, "post", post)
    assert client.call_model(
        "prompt",
        model_profile="local-test",
        route_task_type="code",
        max_tokens=64,
        system="system",
        response_format="json",
        response_schema={"type": "object"},
    )["_status"] == 200
    assert posted["path"] == "/admin/models/call"
    assert posted["body"]["response_schema"] == {"type": "object"}
    assert gludd.GluddClient().call_model("prompt")["failed"] is True


def test_daemon_and_hash_embedders(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = embeddings.DaemonEmbedder(dim=2, psk="secret")
    monkeypatch.setattr(daemon._client, "embed", lambda _text: {"embedding": [3, 4]})
    assert daemon.embed("hello") == [3.0, 4.0]

    monkeypatch.setattr(daemon._client, "embed", lambda _text: {})
    with pytest.raises(RuntimeError, match="omitted embedding"):
        daemon.embed("hello")

    monkeypatch.setattr(daemon._client, "embed", lambda _text: {"embedding": [1]})
    with pytest.raises(RuntimeError, match="dimension 1"):
        daemon.embed("hello")

    with pytest.raises(ValueError, match="positive"):
        embeddings.HashEmbedder(0)
    lexical = embeddings.HashEmbedder(8)
    assert lexical.embed("") == [0.0] * 8
    assert lexical.embed("same words") == lexical.embed(" SAME   WORDS ")
    assert pytest.approx(sum(value * value for value in lexical.embed("same words"))) == 1.0


def test_embedding_client_and_vector_store(monkeypatch: pytest.MonkeyPatch) -> None:
    client = embeddings.EmbeddingClient(model_profile="local", timeout=4)
    assert client._profile == "local"
    assert client._timeout == 4
    assert len(client.embed_text("hello")) == 384
    assert len(client.embed_batch(["one", "two"])) == 2
    assert client.cosine_similarity([1.0], [1.0]) == 1.0

    daemon_client = embeddings.EmbeddingClient(psk="secret")
    monkeypatch.setattr(daemon_client._embedder, "embed", lambda _text: [1.0])
    assert daemon_client.embed_text("hello") == [1.0]

    store = embeddings.VectorStore()
    store.add("a", [1.0, 0.0])
    store.add("b", [0.0, 1.0])
    assert len(store) == 2
    assert "a" in store
    assert store.search([1.0, 0.0], k=0)[0][0] == "a"
    assert store.similarity([1.0, 0.0], "a") == 1.0
    assert store.get("a") == [1.0, 0.0]
    assert set(store.list_ids()) == {"a", "b"}
    store.remove("a")
    store.clear()
    assert len(store) == 0


def test_searx_utilities_and_execute_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert searxng.normalise_url("example.test/") == "http://example.test"
    assert searxng.engines_per_category("flights").startswith("google_flights")
    assert searxng.engines_per_category("unknown") == "google,wikipedia"
    assert searxng.extract_price("from $ 1,234.50") == 1234.5
    assert searxng.extract_price("none") is None
    assert searxng.extract_stars("rated 4.5 stars") == 4.5
    assert searxng.extract_stars("none") is None
    url = searxng.build_search_url("https://search.test/", "hello world", "general")
    assert url.startswith("https://search.test/search?")

    class Connector:
        def _get(
            self,
            _path: str,
            params: dict[str, str | int] | None = None,
        ) -> tuple[int, dict[str, Any]]:
            assert params is not None and params["q"] == "hello world"
            return 200, {"results": [{"url": "https://result.test"}]}

    monkeypatch.setattr(searxng, "_connector", lambda *_args: Connector())
    rows, raw, returned_url = searxng.execute_search(url)
    assert rows == raw == [{"url": "https://result.test"}]
    assert returned_url == url

    monkeypatch.setattr(searxng, "_connector", _raising(RuntimeError("offline")))
    assert searxng.execute_search(url) == ([], [], url)
    monkeypatch.setattr(searxng, "_urlparse", _raising(ValueError("bad URL")))
    assert searxng.execute_search("bad") == ([], [], "bad")


def test_searx_response_and_health_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_score = searxng.SearxResult.from_raw({"url": "u", "score": object()})
    assert invalid_score.score == 0.0
    response = searxng.SearxResponse(
        query="q",
        results=[
            searxng.SearxResult(url="u", title="t", snippet="s", engine="e"),
            searxng.SearxResult(url="", engine="e"),
        ],
    )
    assert response.urls == ["u"]
    assert response.titles == ["t", ""]
    assert response.snippets == ["s", ""]
    assert response.engines == ["e"]

    client = searxng.SearXNGClient()
    monkeypatch.setattr(client._connector, "health", _raising(RuntimeError("offline")))
    assert client.health() == {"ok": False, "detail": "offline"}
