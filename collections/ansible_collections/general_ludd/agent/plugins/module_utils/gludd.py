"""
Shared daemon-API client shim for general_ludd.agent modules.

Every module that talks to the daemon imports this module.  It provides:
  - GluddClient: HTTP transport (PSK auth, timeouts, error mapping)
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

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
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


def typed_operation_argument_spec(operations: tuple[str, ...]) -> dict[str, Any]:
    """Return the shared argument contract for domain operation modules."""
    return {
        "operation": {
            "type": "str",
            "required": True,
            "choices": list(operations),
        },
        "request": {"type": "dict", "required": True},
        "daemon_url": {"type": "str", "default": DEFAULT_DAEMON_URL},
        "psk": {"type": "str", "default": "", "no_log": True},
        "timeout": {"type": "int", "default": DEFAULT_TIMEOUT},
        "idempotency_key": {"type": "str", "default": ""},
    }


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
    text: str | None,
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

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Return the legacy ``(status, body)`` shape over the shared transport."""
        body = self.get(path, params=params)
        raw_status = body.get("_status", 0)
        status = raw_status if isinstance(raw_status, int) else 0
        payload = dict(body)
        payload.pop("_status", None)
        return status, payload

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url=self._url(path), data=data, headers=self._headers(), method="POST")
        return self._send(req)

    def patch(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url=self._url(path), data=data, headers=self._headers(), method="PATCH")
        return self._send(req)

    def delete(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a DELETE request through the same authenticated transport."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url=self._url(path),
            data=data,
            headers=self._headers(),
            method="DELETE",
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                status = exc.code
            finally:
                exc.close()
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
            status = result.get("_status", 0)
            return isinstance(status, int) and status == 200
        except Exception:
            return False

    def health(self) -> dict[str, Any]:
        """Return structured health while preserving transport error detail."""
        status, body = self._get("/healthz")
        if status == 200:
            return {"ok": True, "status": status, "body": body}
        detail = body.get("_error") or body.get("detail") or f"HTTP {status}"
        return {"ok": False, "status": status, "detail": str(detail)}

    def call_model(
        self,
        prompt: str,
        *,
        model_profile: str | None = None,
        route_task_type: str | None = None,
        max_tokens: int = 2048,
        system: str | None = None,
        response_format: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the shared daemon model service over authenticated HTTP.

        Collection processes never import or instantiate model backends.  The
        caller may reuse this client for multiple requests, keeping endpoint,
        authentication, timeout, and response handling in one stdlib-only seam.
        """
        if not self._psk:
            return error_result(
                "authenticated model calls require an explicit psk",
                _status=0,
                _error="missing psk",
            )
        payload: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if model_profile:
            payload["model_profile"] = model_profile
        if route_task_type:
            payload["route_task_type"] = route_task_type
        if system:
            payload["system"] = system
        if response_format:
            payload["response_format"] = response_format
        if response_schema is not None:
            payload["response_schema"] = response_schema
        return self.post("/admin/models/call", payload)


def run_typed_daemon_operation(
    module: Any,
    *,
    namespace: str,
    endpoint: str,
    client_factory: Callable[..., GluddClient] = GluddClient,
    planned_result: dict[str, Any] | None = None,
) -> None:
    """Execute a bounded, idempotent domain operation for an Ansible module.

    Domain collections share the transport and failure contract while retaining
    their own allowlisted operation argument.  ``client_factory`` is injectable
    so collection tests can keep transport fully offline.
    """
    operation: str = module.params["operation"]
    request: dict[str, Any] = module.params["request"]
    timeout: int = module.params["timeout"]
    if timeout < 1 or timeout > DEFAULT_TIMEOUT:
        module.fail_json(
            **error_result(
                f"timeout must be between 1 and {DEFAULT_TIMEOUT} seconds"
            )
        )
        return
    if module.check_mode:
        module.exit_json(
            changed=False,
            operation=operation,
            result=planned_result or {},
        )
        return

    encoded = json.dumps(
        {"operation": operation, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    idempotency_key = module.params["idempotency_key"] or (
        f"{namespace}:{hashlib.sha256(encoded).hexdigest()}"
    )
    client = client_factory(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=timeout,
    )
    response = client.post(
        endpoint,
        {
            "operation": operation,
            "request": request,
            "timeout_seconds": float(timeout),
            "idempotency_key": idempotency_key,
        },
    )
    status = response.get("_status", 0)
    if response.get("_error") or status not in (200, 201):
        detail = response.get("detail") or response.get("_error") or f"HTTP {status}"
        module.fail_json(
            **error_result(
                f"{namespace} operation failed: {detail}",
                status=status,
            )
        )
        return
    result = {key: value for key, value in response.items() if not key.startswith("_")}
    module.exit_json(changed=False, result=result, operation=operation)
