"""
Shared daemon-API client shim for general_ludd.agent modules.

Every module that talks to the daemon imports this module.  It provides:
  - GluddClient: HTTP transport (PSK auth, timeouts, error mapping)
  - LOCAL_TRANSPORT: direct in-process call path (same venv only)
  - error_result / ok_result: uniform return helpers

PSK is read from the ``psk`` parameter (marked no_log).  GluddClient
does NOT fall back to the ``GLUDD_AUTH_PSK`` env var — the caller must
pass the PSK explicitly so a module that omits the psk param cannot
silently scavenge admin credentials from the process environment.
Never log the PSK.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params.get("timeout", 30),
    )
    resp = client.get("/api/todos")
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DAEMON_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Return-value helpers
# ---------------------------------------------------------------------------


def ok_result(data: dict[str, Any], changed: bool = False) -> dict[str, Any]:
    """Return a successful Ansible-style result dict."""
    result = {"failed": False, "changed": changed}
    result.update(data)
    return result


def error_result(msg: str, **extra: Any) -> dict[str, Any]:
    """Return a failed Ansible-style result dict."""
    result = {"failed": True, "changed": False, "msg": msg}
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# Structured-output parsing helpers (model output often wraps JSON in fences)
# ---------------------------------------------------------------------------

# Matches a fenced block:  ```json\n ... \n```  or  ``` \n ... \n```
_FENCE_RE = re.compile(
    r"^\s*```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(?P<body>.*?)\r?\n?```[ \t]*\s*$",
    re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    """Remove a leading/trailing Markdown code fence from ``text``.

    Handles fences with a language hint (```` ```json ````) or bare (```` ``` ````).
    If no surrounding fence is present the text is returned unchanged (stripped).
    Never raises.
    """
    if not isinstance(text, str):
        return text
    match = _FENCE_RE.match(text)
    if match is not None:
        return match.group("body").strip()
    return text.strip()


def parse_structured(
    text: str,
    schema: dict[str, Any] | None = None,
) -> tuple[Any | None, str | None]:
    """Fence-strip ``text`` then ``json.loads`` it.

    Returns ``(obj, None)`` on success or ``(None, reason)`` on failure.
    ``schema`` is accepted for forward compatibility (callers may validate
    against it) but is not enforced here.  Never raises.
    """
    if text is None:
        return None, "empty model output (None)"
    try:
        cleaned = strip_code_fences(text)
    except Exception as exc:
        return None, f"fence-strip failed: {exc}"
    if not cleaned:
        return None, "empty model output after fence strip"
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, f"not valid JSON: {exc}"
    return obj, None


# ---------------------------------------------------------------------------
# HTTP transport (no third-party deps — urllib only so ansible modules work)
# ---------------------------------------------------------------------------


class GluddClient:
    """Thin HTTP client for the general_ludd daemon API.

    Uses only stdlib ``urllib`` so it works inside Ansible module execution
    without requiring ``requests`` in the managed-node venv.

    Parameters
    ----------
    base_url:
        Base URL of the daemon, e.g. ``http://localhost:8000``.
    psk:
        Pre-shared key for the ``X-PSK`` auth header.  Treated as a secret;
        never put it in log output.
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_DAEMON_URL,
        psk: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._psk = psk  # never log
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        psk = self._psk
        if psk:
            # The daemon middleware authenticates on "Authorization: Bearer <psk>".
            # X-PSK is also sent for any future/legacy header-based check.
            headers["Authorization"] = "Bearer " + psk
            headers["X-PSK"] = psk
        return headers

    def _url(self, path: str) -> str:
        return self.base_url + "/" + path.lstrip("/")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._url(path)
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._send(req)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url=self._url(path), data=data, headers=self._headers(), method="POST")
        return self._send(req)

    def patch(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url=self._url(path), data=data, headers=self._headers(), method="PATCH")
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            status = exc.code
        except urllib.error.URLError as exc:
            return {"_error": str(exc.reason), "_status": 0, "_raw": ""}
        except Exception as exc:
            return {"_error": str(exc), "_status": 0, "_raw": ""}

        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"_raw": raw}

        parsed["_status"] = status
        return parsed

    def reachable(self) -> bool:
        """Return True if /healthz responds 200."""
        try:
            result = self.get("/healthz")
            return result.get("_status", 0) == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Local (in-process) transport — same venv only
# ---------------------------------------------------------------------------


def local_model_call(
    prompt: str,
    model_profile: str | None = None,
    route_task_type: str | None = None,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call the gateway directly when running in the same venv as the daemon.

    Falls back gracefully if ``general_ludd`` is not importable.
    """
    try:
        from general_ludd.models.gateway import ModelGateway  # type: ignore[import]

        gw = ModelGateway()
        text = gw.call_model(
            prompt=prompt,
            model_profile=model_profile,
            task_type=route_task_type,
            max_tokens=max_tokens,
        )
        return ok_result({"text": text, "transport": "local"})
    except ImportError:
        return error_result("general_ludd not importable; use daemon_url for HTTP transport")
    except Exception as exc:
        return error_result(f"local model call failed: {exc}")
