"""
Reusable model client for general_ludd.agent — any collection can import it.

    from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import ModelClient

    client = ModelClient("default")
    response = client.chat([{"role": "user", "content": "Hello"}])
    for event in client.chat_stream([{"role": "user", "content": "Write a haiku"}]):
        print(event)
    embedding = client.embed("What is the meaning of life?")
    profiles = client.list_models()

Environment variables:
  GLUDD_DAEMON_URL — daemon base URL (falls back to DAEMON_URL, then localhost:8000)
  GLUDD_PSK — pre-shared key for daemon auth (empty = no auth)
  GLUDD_MODEL_TIMEOUT — per-request timeout in seconds (default 120)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Generator
from typing import Any

DEFAULT_DAEMON_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 120


def _env_daemon_url() -> str:
    return os.environ.get("GLUDD_DAEMON_URL") or os.environ.get("DAEMON_URL") or DEFAULT_DAEMON_URL


def _env_psk() -> str:
    return os.environ.get("GLUDD_PSK", "")


def _env_timeout() -> int:
    try:
        return int(os.environ.get("GLUDD_MODEL_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


class ModelClient:
    """Synchronous client for the gludd daemon model gateway.

    Parameters
    ----------
    profile_name:
        Model profile ID to use for chat/embed calls.  Default ``"default"``.
    """

    def __init__(self, profile_name: str = "default") -> None:
        self._profile = profile_name
        self._base_url = _env_daemon_url().rstrip("/")
        self._psk = _env_psk()
        self._timeout = _env_timeout()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._psk:
            headers["Authorization"] = "Bearer " + self._psk
            headers["X-PSK"] = self._psk
        return headers

    def _url(self, path: str) -> str:
        return self._base_url + "/" + path.lstrip("/")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        """Synchronous chat completion.

        ``messages`` is an OpenAI-style list of ``{"role": "...", "content": "..."}``
        dicts.  The daemon's single-turn endpoint is mapped: the first ``system``
        message becomes the system prompt, and the content of the last ``user``
        message becomes the prompt body.

        Extra keyword arguments (``max_tokens``, ``temperature``, …) are passed
        through to the daemon as top-level body keys.
        """
        prompt = ""
        system = None
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system = content or system
            elif role == "user":
                prompt = content or prompt

        body: dict[str, Any] = {"prompt": prompt}
        if system:
            body["system"] = system
        if self._profile and self._profile != "default":
            body["model_profile"] = self._profile
        body.update(kwargs)

        return self._post("/admin/models/call", body)

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Generator[dict[str, Any], None, None]:
        """Streaming chat completion via SSE.

        Yields ``dict`` objects parsed from each ``data:`` line in the server's
        SSE response.  The final event has ``done: True`` and includes usage
        metadata.

        ``messages`` is passed directly to the daemon's ``/admin/models/chat-stream``
        endpoint.
        """
        body: dict[str, Any] = {
            "messages": messages,
            "model_profile_id": self._profile,
        }
        body.update(kwargs)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url=self._url("/admin/models/chat-stream"),
            data=data,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                for line_b in resp:
                    line = line_b.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:") :].strip()
                    if not payload_str:
                        continue
                    try:
                        yield json.loads(payload_str)
                    except json.JSONDecodeError:
                        yield {"_raw": payload_str}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            yield {"_error": raw, "_status": exc.code}
        except urllib.error.URLError as exc:
            yield {"_error": str(exc.reason), "_status": 0}
        except Exception as exc:
            yield {"_error": str(exc), "_status": 0}

    def embed(self, texts: str | list[str], *, include_embedding: bool = True) -> dict[str, Any]:
        """Get embeddings for one or more input strings.

        When given a single string, returns a dict with the key ``embedding``
        (the float vector) plus ``embedding_method`` and ``dim``.

        When given a list, returns a dict with the key ``embeddings`` (a list
        of vectors in the same order) plus ``embedding_method`` and ``dim``.
        """
        if isinstance(texts, str):
            return self._embed_single(texts, include_embedding=include_embedding)

        results = []
        method = "unknown"
        dim = 0
        for text in texts:
            single = self._embed_single(text, include_embedding=include_embedding)
            if single.get("_error"):
                return {"_error": single["_error"], "_status": single.get("_status", 0)}
            if results:
                single_vec = single.get("embedding")
                if isinstance(single_vec, list):
                    results.append(single_vec)
            else:
                single_vec = single.get("embedding")
                if isinstance(single_vec, list):
                    results.append(single_vec)
                method = single.get("embedding_method", method)
                dim = single.get("dim", dim)

        return {"embeddings": results, "embedding_method": method, "dim": dim}

    def _embed_single(self, text: str, *, include_embedding: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": text,
            "top_k": 1,
            "include_embedding": include_embedding,
        }
        resp = self._post("/api/embeddings/similar", body)
        if resp.get("_error") is not None and resp.get("_status", 0) != 200:
            return resp

        embedding = resp.get("query_embedding")
        return {
            "embedding": embedding,
            "embedding_method": resp.get("embedding_method", "unknown"),
            "dim": resp.get("query_embedding_dim", len(embedding) if isinstance(embedding, list) else 0),
        }

    def list_models(self) -> dict[str, Any]:
        """Return the list of available model profiles."""
        return self._get("/admin/models")

    def reachable(self) -> bool:
        """Return True if the daemon's health check responds 200."""
        try:
            result = self._get("/healthz")
            return result.get("_status", 0) == 200
        except Exception:
            return False

    def _get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url=self._url(path),
            headers=self._headers(),
            method="GET",
        )
        return self._send(req)

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            url=self._url(path),
            data=data,
            headers=self._headers(),
            method="POST",
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            status = exc.code
        except urllib.error.URLError as exc:
            return {"_error": str(exc.reason), "_status": 0}
        except Exception as exc:
            return {"_error": str(exc), "_status": 0}

        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"_raw": raw}

        parsed["_status"] = status
        return parsed
