"""Baseten model-hosting connector — wraps deployment listing, invocation, health.

This connector bridges gludd to Baseten's two API surfaces:

* **Management API** (``https://api.baseten.co``) — used by :meth:`list_deployments`
  to enumerate deployed models and their statuses via ``GET /v1/models``.
* **Inference API** (``https://inference.baseten.co/v1``) — used by :meth:`invoke`
  to call a deployed model through the OpenAI-compatible ``POST /chat/completions``
  endpoint, where ``model_deployment_id`` is the model name registered with the
  Model APIs surface.

Design constraints (matching the rest of the connector package):

* **Injectable transport.** All HTTP goes through an ``http_request`` callable
  ``(method, url, headers, body) -> (status, json)``. Tests inject a mock; the
  default uses ``httpx`` with a bounded timeout. No ``requests`` dependency.
* **Auth from env.** The Baseten API key is read from the env var named by
  ``config['api_key_env']`` (default ``BASETEN_API_KEY``). The key is sent as
  ``Authorization: Bearer <key>``; the secret itself never appears in config,
  logs, or records.
* **SSRF literal-host block.** ``base_url`` and ``management_url`` are validated
  at construction against loopback / private / link-local / metadata addresses.
* **Strict typing.** No ``Any``, no ``# type: ignore``. Heterogeneous config
  values are typed as ``Mapping[str, str | int | float | bool | None]``.
* **health() never raises.** It always returns a dict.

References
----------
* Inference API: https://docs.baseten.co/reference/inference-api/overview
* Management API: https://docs.baseten.co/reference/management-api/overview
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import TypedDict
from urllib.parse import urlsplit

from general_ludd.connectors._errors import ConnectorConfigError, SSRFError
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Transport contract: (method, url, headers, body) -> (status_code, json_dict)
HttpRequest = Callable[
    [str, str, Mapping[str, str], "bytes | None"],
    "tuple[int, dict[str, object]]",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_BASE_URL = "https://inference.baseten.co/v1"
_DEFAULT_MANAGEMENT_URL = "https://api.baseten.co"
_DEFAULT_API_KEY_ENV = "BASETEN_API_KEY"

# Connector kind — deployments + invocations are a CI/CD-ish pipeline surface.
KIND = "pipeline"


# --------------------------------------------------------------------------- #
# TypedDicts — response shapes (no Any, no type: ignore)
# --------------------------------------------------------------------------- #
class BasetenDeployment(TypedDict, total=False):
    """One deployment of a model, as returned by :meth:`BasetenClient.list_deployments`.

    All fields optional because Baseten's management API does not guarantee every
    key on every deployment payload.
    """

    id: str
    model_id: str
    name: str
    status: str
    environment: str
    created_at: str


class BasetenModel(TypedDict, total=False):
    """Subset of the ``GET /v1/models`` item shape consumed here."""

    id: str
    name: str
    deployments: list[BasetenDeployment]


class BasetenModelsResponse(TypedDict, total=False):
    """``GET /v1/models`` top-level shape (subset)."""

    id: str
    items: list[BasetenModel]


class BasetenHealthResult(TypedDict):
    """Shape returned by :meth:`BasetenClient.health`."""

    ok: bool
    reachable: bool
    api_key_valid: bool
    detail: str
    source: str


class BasetenConfig(TypedDict, total=False):
    """Constructor config accepted by :class:`BasetenClient`.

    ``api_key_env`` names the env var holding the Baseten API key (the secret
    itself is never placed in config). ``base_url`` is the inference API base,
    ``management_url`` is the management API base.
    """

    name: str
    api_key_env: str
    base_url: str
    management_url: str


# Heterogeneous config-value type — the documented shape for connector config.
ConfigValue = str | int | float | bool | None
HeterogeneousConfig = Mapping[str, ConfigValue]


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #
def _validate_url(url: str, field: str) -> str:
    """Validate ``url`` against SSRF rules and return it normalized (no trailing slash).

    No DNS resolution is performed; a literal IP host is checked against blocked
    ranges, a known-bad metadata hostname is rejected by name, any other
    hostname is allowed (the transport resolves it at call time).
    """
    if not url or not isinstance(url, str):
        raise ConnectorConfigError(f"{field} is required")

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(
            f"{field} must be http(s), got scheme {parts.scheme!r}"
        )

    if is_url_blocked(url, scheme_allowlist=("http", "https")):
        host = parts.hostname or ""
        raise SSRFError(f"{field} host {host!r} is blocked")

    return url.rstrip("/")


# --------------------------------------------------------------------------- #
# Default httpx transport
# --------------------------------------------------------------------------- #
def _default_http_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, object]]:
    """Real transport — httpx with a bounded timeout. Imported lazily.

    Kept tiny so tests never need it (they inject a mock).
    """
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=dict(headers),
        content=body,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return resp.status_code, payload


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class BasetenConfigError(ConnectorConfigError):
    """Raised when Baseten connector config is malformed or credentials missing."""


class BasetenInvocationError(RuntimeError):
    """Raised when a Baseten API call returns a non-2xx status.

    Carries the HTTP status code and a non-sensitive detail string. The API key
    is never embedded in the message.
    """


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class BasetenClient:
    """Wrap Baseten's model-hosting API for deployment listing, invocation, health.

    Parameters
    ----------
    config:
        Optional :class:`BasetenConfig` (or any ``Mapping[str, str|int|float|bool|None]``).
        Keys consumed: ``api_key_env``, ``base_url``, ``management_url``, ``name``.
    http_request:
        Optional injectable transport callable. Tests inject a mock; production
        uses :func:`_default_http_request` (httpx).

    Raises
    ------
    BasetenConfigError
        If ``api_key_env`` names an env var that is not set, or if either URL
        fails SSRF validation.
    """

    KIND: str = KIND

    def __init__(
        self,
        config: HeterogeneousConfig | None = None,
        http_request: HttpRequest | None = None,
    ) -> None:
        cfg: dict[str, ConfigValue] = dict(config or {})

        # Resolve api_key_env (string-typed) — the env var must be present.
        api_key_env_name = cfg.get("api_key_env", _DEFAULT_API_KEY_ENV)
        if not isinstance(api_key_env_name, str) or not api_key_env_name:
            raise BasetenConfigError("api_key_env must be a non-empty string")
        self._api_key_env: str = api_key_env_name
        # Fail fast at construction if the env var is missing — a connector
        # with no credentials cannot serve any method.
        if not os.environ.get(self._api_key_env):
            raise BasetenConfigError(
                f"missing env var {self._api_key_env!r} (Baseten API key)"
            )

        # Resolve URLs (string-typed) with SSRF validation.
        base_url_raw = cfg.get("base_url", _DEFAULT_BASE_URL)
        if not isinstance(base_url_raw, str):
            raise BasetenConfigError("base_url must be a string")
        self._base_url: str = _validate_url(base_url_raw, "base_url")

        mgmt_raw = cfg.get("management_url", _DEFAULT_MANAGEMENT_URL)
        if not isinstance(mgmt_raw, str):
            raise BasetenConfigError("management_url must be a string")
        self._management_url: str = _validate_url(mgmt_raw, "management_url")

        # Connector identity name (used in health()/source attribution).
        name_val = cfg.get("name", "baseten")
        self.name: str = str(name_val) if isinstance(name_val, str) else "baseten"

        # Transport (injectable for tests).
        self._http_request: HttpRequest = http_request or _default_http_request

    # -- auth ----------------------------------------------------------------

    def _api_key(self) -> str:
        """Read the API key from the configured env var. Never logged."""
        return os.environ.get(self._api_key_env, "")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- health --------------------------------------------------------------

    def health(self) -> BasetenHealthResult:
        """Probe Baseten reachability + API key validity. Never raises.

        Pings ``GET {management_url}/v1/models``:

        - 2xx → ``ok=True, reachable=True, api_key_valid=True``
        - 401/403 → ``ok=False, api_key_valid=False`` (reachable but bad key)
        - other 4xx → ``ok=False, reachable=True`` (key indeterminate)
        - 5xx / transport error → ``ok=False, reachable=False`` (Baseten outage)
        """
        url = f"{self._management_url}/v1/models"
        try:
            status, _payload = self._http_request("GET", url, self._headers(), None)
        except OSError as exc:
            return self._health_false(
                reachable=False,
                api_key_valid=False,
                detail=f"transport error: {type(exc).__name__}",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._health_false(
                reachable=False,
                api_key_valid=False,
                detail=f"unexpected error: {type(exc).__name__}",
            )

        if status >= 500:
            return self._health_false(
                reachable=False,
                api_key_valid=False,
                detail=f"baseten outage (http {status})",
            )
        if status in (401, 403):
            return self._health_false(
                reachable=True,
                api_key_valid=False,
                detail=f"invalid api key (http {status})",
            )
        if status >= 400:
            return self._health_false(
                reachable=True,
                api_key_valid=False,
                detail=f"unexpected http {status}",
            )
        return {
            "ok": True,
            "reachable": True,
            "api_key_valid": True,
            "detail": "ok",
            "source": self.name,
        }

    @staticmethod
    def _health_false(
        *, reachable: bool, api_key_valid: bool, detail: str
    ) -> BasetenHealthResult:
        return {
            "ok": False,
            "reachable": reachable,
            "api_key_valid": api_key_valid,
            "detail": detail,
            "source": "baseten",
        }

    # -- list_deployments ----------------------------------------------------

    def list_deployments(self) -> list[BasetenDeployment]:
        """List deployed models via ``GET {management_url}/v1/models``.

        Returns a flat list of :class:`BasetenDeployment` across all models —
        each carries ``id``, ``model_id``, ``status``, ``environment``, etc.

        Raises
        ------
        BasetenInvocationError
            On any non-2xx response (404 unknown, 401 invalid key, 5xx outage).
        """
        url = f"{self._management_url}/v1/models"
        status, payload = self._http_request("GET", url, self._headers(), None)
        if status >= 400:
            raise BasetenInvocationError(
                f"list_deployments failed: http {status}"
            )
        return self._normalize_deployments(payload)

    def _normalize_deployments(self, payload: dict[str, object]) -> list[BasetenDeployment]:
        """Flatten the ``GET /v1/models`` payload into a list of deployments.

        Tolerates either ``{"items": [...]}`` (paginated) or a bare list of
        models, and tolerates per-model shapes with or without ``deployments``.
        """
        models: list[Mapping[str, object]] = []
        items_raw: object = payload.get("items") if isinstance(payload, Mapping) else None
        if isinstance(items_raw, list):
            models = [m for m in items_raw if isinstance(m, Mapping)]
        elif isinstance(payload, list):
            models = [m for m in payload if isinstance(m, Mapping)]

        out: list[BasetenDeployment] = []
        for model in models:
            model_id = model.get("id")
            model_name = model.get("name")
            deployments_raw = model.get("deployments")
            if not isinstance(deployments_raw, list):
                continue
            for dep in deployments_raw:
                if not isinstance(dep, Mapping):
                    continue
                normalized = self._normalize_deployment(dep, model_id, model_name)
                out.append(normalized)
        return out

    @staticmethod
    def _normalize_deployment(
        dep: Mapping[str, object],
        model_id: object,
        model_name: object,
    ) -> BasetenDeployment:
        """Coerce a raw deployment Mapping into the typed shape."""
        result: BasetenDeployment = {}
        dep_id = dep.get("id")
        if isinstance(dep_id, str):
            result["id"] = dep_id
        if isinstance(model_id, str):
            result["model_id"] = model_id
        if isinstance(model_name, str):
            result["name"] = model_name
        status = dep.get("status")
        if isinstance(status, str):
            result["status"] = status
        env = dep.get("environment")
        if isinstance(env, str):
            result["environment"] = env
        created = dep.get("created_at")
        if isinstance(created, str):
            result["created_at"] = created
        return result

    # -- invoke --------------------------------------------------------------

    def invoke(
        self,
        model_deployment_id: str,
        inputs: Mapping[str, object],
    ) -> dict[str, object]:
        """Invoke a deployed model via the OpenAI-compatible inference endpoint.

        Posts to ``{base_url}/chat/completions`` with a body of::

            {"model": model_deployment_id, **inputs}

        so callers pass ``inputs={"messages": [...]}`` (or any other OpenAI
        chat-completions fields — ``temperature``, ``max_tokens``, etc.).

        Parameters
        ----------
        model_deployment_id:
            The Baseten model name (for Model APIs) or deployment identifier.
        inputs:
            Caller-supplied body fields, merged into the request alongside
            ``model``. ``messages`` is the typical key.

        Returns
        -------
        dict[str, object]
            The raw chat-completion response payload.

        Raises
        ------
        BasetenInvocationError
            On any non-2xx response. ``404`` → unknown deployment,
            ``401`` → invalid key, ``5xx`` → Baseten-side outage.
        """
        if not isinstance(model_deployment_id, str) or not model_deployment_id:
            raise BasetenConfigError("model_deployment_id must be a non-empty string")

        url = f"{self._base_url}/chat/completions"
        body_dict: dict[str, object] = {"model": model_deployment_id}
        body_dict.update(dict(inputs))
        encoded = json.dumps(body_dict).encode("utf-8")

        try:
            status, payload = self._http_request(
                "POST", url, self._headers(), encoded
            )
        except OSError as exc:
            raise BasetenInvocationError(
                f"invoke transport error: {type(exc).__name__}"
            ) from exc

        if status >= 400:
            raise BasetenInvocationError(
                f"invoke({model_deployment_id!r}) failed: http {status}"
            )

        return payload
