"""Lambda Labs GPU cloud connector.

Self-contained client for the Lambda Labs Cloud API
(https://cloud.lambdalabs.com/api/v1). No imports from sibling connectors or a
shared base, so the file can be dropped in and tested in isolation.

Lambda Labs offers two distinct surfaces:

* the **GPU Cloud** (on-demand H100 / A100 / B200 instances) at
  ``https://cloud.lambdalabs.com/api/v1`` — the surface this module covers;
* **hosted models**, an OpenAI-compatible inference API at
  ``https://api.lambdalabs.ai/v1`` — out of scope here (it is a plain
  OpenAI client pointed at that base URL).

Cloud API reference: https://docs.lambda.ai/api/cloud

Endpoints used
--------------
* ``GET /instances``                        -> running GPU instances.
* ``GET /instance-types``                   -> available GPU SKUs + pricing.
* ``POST /instance-operations/launch``      -> launch one or more instances.
* ``POST /instance-operations/terminate``   -> terminate instances by id.

SECURITY NOTES:
  - The API key is read ONLY from the environment variable named by
    ``config['api_key_env']`` (default ``LAMBDALABS_API_KEY``). It is never
    accepted inline, never hardcoded, and never written to any record, log
    line, or raised error.
  - ``base_url`` is SSRF-guarded via the literal-host check
    (:func:`general_ludd.security.ssrf.is_url_blocked`): private/loopback /
    link-local / cloud-metadata hosts are rejected.
  - HTTP is performed through httpx with an injectable transport; tests pass
    ``httpx.MockTransport``. Requests are time-bound and never use a shell.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TypedDict, cast

import httpx

from general_ludd.security.ssrf import is_url_blocked

__all__ = [
    "InstanceRegion",
    "InstanceSpecs",
    "InstanceTypeRef",
    "LambdaInstance",
    "LambdaInstanceType",
    "LambdaLabsClient",
    "LambdaLabsError",
]

_DEFAULT_BASE_URL = "https://cloud.lambdalabs.com/api/v1"
_DEFAULT_API_KEY_ENV = "LAMBDALABS_API_KEY"
_DEFAULT_TIMEOUT = 15.0


class LambdaLabsError(RuntimeError):
    """Raised on Lambda Labs API failures (non-2xx status, malformed body)."""


# --------------------------------------------------------------------------- #
# Typed response shapes (Lambda Labs Cloud API).
# --------------------------------------------------------------------------- #
class InstanceSpecs(TypedDict, total=False):
    """Hardware spec block embedded in an instance type."""

    gpus: int
    gpu_type: str
    vcpus: int
    memory_gib: int
    storage_gib: int


class InstanceRegion(TypedDict, total=False):
    """Region descriptor attached to a running instance."""

    name: str
    description: str


class InstanceTypeRef(TypedDict, total=False):
    """The ``instance_type`` field embedded in a running instance (name only)."""

    name: str


class LambdaInstance(TypedDict, total=False):
    """A GPU instance as returned by ``GET /instances``."""

    id: str
    name: str
    status: str
    instance_type: InstanceTypeRef
    ip: str
    region: InstanceRegion
    hostname: str
    ssh_key_names: list[str]
    file_system_names: list[str]


class LambdaInstanceType(TypedDict, total=False):
    """An available GPU SKU with specs + pricing from ``GET /instance-types``."""

    name: str
    specs: InstanceSpecs
    context: list[str]
    price_cents_per_hour: int
    regions_with_capacity_available: list[str]


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class LambdaLabsClient:
    """Client for the Lambda Labs GPU Cloud API.

    All API key material is sourced from the environment at call time. HTTP is
    performed via httpx; pass ``transport=httpx.MockTransport(...)`` in tests.

    Example
    -------
    >>> client = LambdaLabsClient()                 # reads LAMBDALABS_API_KEY
    >>> running = client.list_instances()           # GET /instances
    >>> skus = client.list_instance_types()         # GET /instance-types
    >>> box = client.launch_instance(               # POST /instance-operations/launch
    ...     "gpu_8x_h100_sxm4", "training", "us-east-1", ssh_key_names=["dev"]
    ... )
    >>> client.terminate_instance(box["id"])        # POST .../terminate
    """

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        cfg = dict(config or {})
        self.api_key_env: str = str(cfg.get("api_key_env", _DEFAULT_API_KEY_ENV))
        self._timeout: float = _coerce_timeout(cfg.get("timeout", _DEFAULT_TIMEOUT))
        base_url = str(cfg.get("base_url", _DEFAULT_BASE_URL))
        _validate_base_url(base_url)
        # Keep a leading-slash-free, trailing-slash-free prefix for join().
        self.base_url: str = base_url.rstrip("/")
        self.name: str = str(cfg.get("name", "lambda_labs"))
        self._transport = transport

    # -- secrets -----------------------------------------------------------
    def _api_key(self) -> str | None:
        key = os.environ.get(self.api_key_env)
        return key or None

    def _require_key(self) -> str:
        key = self._api_key()
        if not key:
            raise LambdaLabsError(f"environment variable {self.api_key_env} is not set")
        return key

    def _headers(self, key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "general-ludd-lambda-labs",
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    # -- request helpers ---------------------------------------------------
    def _get(self, path: str, *, action: str) -> httpx.Response:
        key = self._require_key()
        with self._client() as client:
            resp = client.get(f"{self.base_url}{path}", headers=self._headers(key))
        _ensure_ok(resp, action=action)
        return resp

    def _post(self, path: str, body: Mapping[str, object], *, action: str) -> httpx.Response:
        key = self._require_key()
        with self._client() as client:
            resp = client.post(
                f"{self.base_url}{path}",
                json=body,
                headers=self._headers(key),
            )
        _ensure_ok(resp, action=action)
        return resp

    @staticmethod
    def _json(resp: httpx.Response) -> object:
        """Parse the response body as JSON; empty bodies become ``None``.

        Raises :class:`LambdaLabsError` on malformed JSON.
        """
        text = resp.text
        if not text:
            return None
        try:
            parsed: object = resp.json()
        except ValueError as exc:
            raise LambdaLabsError(f"malformed JSON response: {exc}") from exc
        return parsed

    # -- public API --------------------------------------------------------
    def list_instances(self) -> list[LambdaInstance]:
        """List running GPU instances (``GET /instances``).

        The Lambda Labs API wraps results in ``{"data": [...]}``; the returned
        list is the unwrapped array of :class:`LambdaInstance` dicts.
        """
        resp = self._get("/instances", action="list_instances")
        body = self._json(resp)
        data = _field(body, "data")
        if data is None:
            return []
        return [_to_instance(item) for item in _as_list(data)]

    def list_instance_types(self) -> list[LambdaInstanceType]:
        """List available GPU SKUs with specs + pricing (``GET /instance-types``).

        The API returns ``{"data": {"<type_name>": {"instance_type": {...}}, ...}}``;
        the returned list is the unwrapped set of :class:`LambdaInstanceType`
        dicts (H100, A100, etc.).
        """
        resp = self._get("/instance-types", action="list_instance_types")
        body = self._json(resp)
        data = _field(body, "data")
        if data is None:
            return []
        entries = _as_mapping(data)
        out: list[LambdaInstanceType] = []
        for value in entries.values():
            inner = _field(value, "instance_type")
            if inner is None:
                continue
            out.append(_to_instance_type(inner))
        return out

    def launch_instance(
        self,
        instance_type: str,
        name: str,
        region: str,
        *,
        ssh_key_names: list[str] | None = None,
        quantity: int = 1,
        file_system_names: list[str] | None = None,
    ) -> LambdaInstance:
        """Launch a GPU instance (``POST /instance-operations/launch``).

        The Lambda Labs launch API returns only the launched instance id(s); the
        returned :class:`LambdaInstance` is therefore populated with ``id`` and
        ``name`` only — call :meth:`list_instances` to refresh full details.
        """
        body: dict[str, object] = {
            "name": name,
            "instance_type": instance_type,
            "region_name": region,
            "quantity": quantity,
            "ssh_key_names": list(ssh_key_names) if ssh_key_names else [],
            "file_system_names": list(file_system_names) if file_system_names else [],
        }
        resp = self._post("/instance-operations/launch", body, action="launch_instance")
        parsed = self._json(resp)
        data_obj = _field(parsed, "data")
        ids_obj = _field(data_obj, "instance_ids") if data_obj is not None else None
        ids = _as_list(ids_obj) if ids_obj is not None else []
        if not ids:
            raise LambdaLabsError("launch did not return any instance_ids")
        first = ids[0]
        if not isinstance(first, str):
            raise LambdaLabsError("launch returned a non-string instance id")
        return LambdaInstance(id=first, name=name)

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate a GPU instance (``POST /instance-operations/terminate``).

        The terminate endpoint returns ``204 No Content`` with an empty body;
        this method raises :class:`LambdaLabsError` on a non-2xx status and
        returns ``None`` on success.
        """
        self._post(
            "/instance-operations/terminate",
            {"instance_ids": [instance_id]},
            action="terminate_instance",
        )

    def health(self) -> dict[str, object]:
        """Probe API key validity + reachability. Never raises.

        Returns a dict with keys:

        * ``ok`` (bool)            — overall probe success.
        * ``detail`` (str)         — human-readable status / error class.
        * ``reachable`` (bool|None)— did the API answer at all?
        * ``api_key_valid`` (bool|None)— did the API accept the key (200)?
        """
        key = self._api_key()
        if key is None:
            return {
                "ok": False,
                "detail": f"{self.api_key_env} not set",
                "reachable": None,
                "api_key_valid": None,
            }
        try:
            with self._client() as client:
                resp = client.get(f"{self.base_url}/instances", headers=self._headers(key))
        except Exception as exc:  # health must never raise
            return {
                "ok": False,
                "detail": _safe_err(exc),
                "reachable": False,
                "api_key_valid": None,
            }
        status = resp.status_code
        if 200 <= status < 300:
            return {
                "ok": True,
                "detail": f"HTTP {status}",
                "reachable": True,
                "api_key_valid": True,
            }
        if status in (401, 403):
            return {
                "ok": False,
                "detail": f"HTTP {status} (api key rejected)",
                "reachable": True,
                "api_key_valid": False,
            }
        return {
            "ok": False,
            "detail": f"HTTP {status}",
            "reachable": True,
            "api_key_valid": None,
        }


# --------------------------------------------------------------------------- #
# module-private parsing / validation helpers (no `Any` anywhere)
# --------------------------------------------------------------------------- #
def _validate_base_url(base_url: str) -> None:
    """Reject loopback / private / metadata hosts in *base_url*."""
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"base_url host is blocked (loopback/private/metadata): {base_url!r}"
        )


def _ensure_ok(resp: httpx.Response, *, action: str) -> None:
    """Raise :class:`LambdaLabsError` if *resp* is not 2xx."""
    if not (200 <= resp.status_code < 300):
        raise LambdaLabsError(f"{action} failed: HTTP {resp.status_code}")


def _coerce_timeout(value: object) -> float:
    """Coerce a config timeout (int|float|str|other) to a finite float."""
    if isinstance(value, bool):
        return _DEFAULT_TIMEOUT
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return _DEFAULT_TIMEOUT
    return _DEFAULT_TIMEOUT


def _as_mapping(value: object) -> Mapping[str, object]:
    """Narrow *value* to ``Mapping[str, object]`` or raise."""
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    raise LambdaLabsError("expected a JSON object in response")


def _as_list(value: object) -> list[object]:
    """Narrow *value* to ``list[object]`` or raise."""
    if isinstance(value, list):
        return value
    raise LambdaLabsError("expected a JSON array in response")


def _field(obj: object, key: str) -> object | None:
    """Return ``obj[key]`` if *obj* is a mapping with that key, else ``None``."""
    if not isinstance(obj, Mapping):
        return None
    return cast("Mapping[str, object]", obj).get(key)


def _to_instance(raw: object) -> LambdaInstance:
    """Build a :class:`LambdaInstance` from a raw instance object."""
    if not isinstance(raw, Mapping):
        raise LambdaLabsError("instance entry is not a JSON object")
    return cast("LambdaInstance", dict(cast("Mapping[str, object]", raw)))


def _to_instance_type(raw: object) -> LambdaInstanceType:
    """Build a :class:`LambdaInstanceType` from a raw instance-type object."""
    if not isinstance(raw, Mapping):
        raise LambdaLabsError("instance_type entry is not a JSON object")
    return cast("LambdaInstanceType", dict(cast("Mapping[str, object]", raw)))


def _safe_err(exc: Exception) -> str:
    """Error label that never leaks the request URL or credentials."""
    return type(exc).__name__
