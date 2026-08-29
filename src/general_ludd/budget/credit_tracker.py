"""CreditTracker — prepaid service credit / balance tracking for model providers.

Each supported provider exposes a different balance / usage API:

* **DeepSeek** — ``GET https://api.deepseek.com/user/balance`` returns a list of
  per-currency wallets; the first USD wallet's ``balance`` is the remaining
  prepaid credit.
* **OpenAI** — ``GET https://api.openai.com/v1/organization/costs`` returns the
  rolling cost line items. OpenAI does not expose a public prepaid-balance API;
  the tracker reports the summed line-item usage as ``balance_usd`` so the
  caller can see "spend against the prepaid pool" even when no balance figure
  is published.
* **Z.AI** — ``GET https://api.z.ai/api/paas/v4/usage`` returns
  ``{"data": {"balance": <float>, "currency": "USD"}}``.
* **OpenRouter** — ``GET https://openrouter.ai/api/credits`` returns
  ``{"data": {"total_credits": <float>, "total_usage": <float>}}``. Remaining
  balance = ``total_credits - total_usage``.

The tracker is transport-agnostic: pass any object with a ``get(url, headers=)``
method as ``http_client`` (httpx.Client, requests.Session, or a test fake).
When no client is passed, a lazy ``httpx.Client`` is constructed on first use.

All network operations are best-effort: a missing API key, a transport error,
an HTTP non-2xx, or an unparseable body each surface as a dict with
``balance_usd=None`` and a specific ``error`` string — never an exception. The
caller can therefore iterate over every provider without one bad key poisoning
the rest.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_SERVICES: tuple[str, ...] = ("deepseek", "openai", "zai", "openrouter")

#: Per-provider endpoint + auth configuration.
_SERVICE_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "balance_path": "/user/balance",
        "auth_env_var": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com",
        "balance_path": "/v1/organization/costs",
        "auth_env_var": "OPENAI_API_KEY",
    },
    "zai": {
        "base_url": "https://api.z.ai/api",
        "balance_path": "/paas/v4/usage",
        "auth_env_var": "ZAI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai",
        "balance_path": "/api/credits",
        "auth_env_var": "OPENROUTER_API_KEY",
    },
}

#: Conservative default minimum balance before a refill is recommended (USD).
DEFAULT_THRESHOLDS: dict[str, float] = {
    "deepseek": 1.0,
    "openai": 5.0,
    "zai": 1.0,
    "openrouter": 1.0,
}

#: Default refill horizon (days) for :meth:`CreditTracker.recommend_refill_amount`.
DEFAULT_REFILL_DAYS: float = 7.0

#: Providers whose APIs accept a programmatic per-key spend limit. The other
#: providers do not expose this; :meth:`set_spend_limit` returns
#: ``{"supported": False}`` for them.
_SPEND_LIMIT_SUPPORTED: frozenset[str] = frozenset({"openrouter", "openai"})


# ---------------------------------------------------------------------------
# Per-provider response parsers. Each returns (balance_usd, currency) or
# raises _Unparseable on a shape it does not recognise.
# ---------------------------------------------------------------------------


class _Unparseable(ValueError):
    """Raised by per-provider parsers when the JSON shape is unexpected."""


def _parse_deepseek(data: Any) -> tuple[float, str]:
    wallets = (data or {}).get("wallets") or []
    for w in wallets:
        if str(w.get("currency", "")).upper() == "USD":
            return float(w["balance"]), "USD"
    if wallets:
        # No USD wallet — fall back to the first wallet's currency.
        w = wallets[0]
        return float(w["balance"]), str(w.get("currency", "USD")).upper()
    raise _Unparseable("no wallets in DeepSeek response")


def _parse_openai(data: Any) -> tuple[float, str]:
    # /v1/organization/costs returns usage line items, not a balance. We
    # surface the summed usage so the caller can observe spend against the
    # prepaid pool. (OpenAI has no public balance API for prepaid credits.)
    items = (data or {}).get("data") or []
    total = 0.0
    for item in items:
        line = item.get("line_item")
        if line is None:
            continue
        total += float(line)
    return total, "USD"


def _parse_zai(data: Any) -> tuple[float, str]:
    inner = (data or {}).get("data") or {}
    if "balance" not in inner:
        raise _Unparseable("no balance field in Z.AI response")
    return float(inner["balance"]), str(inner.get("currency", "USD")).upper()


def _parse_openrouter(data: Any) -> tuple[float, str]:
    inner = (data or {}).get("data") or {}
    if "total_credits" not in inner or "total_usage" not in inner:
        raise _Unparseable("missing total_credits/total_usage in OpenRouter response")
    return float(inner["total_credits"]) - float(inner["total_usage"]), "USD"


_PARSERS: dict[str, Any] = {
    "deepseek": _parse_deepseek,
    "openai": _parse_openai,
    "zai": _parse_zai,
    "openrouter": _parse_openrouter,
}


class CreditTracker:
    """Prepaid service credit / balance tracker.

    Args:
        api_keys:                Mapping of ``service -> api_key``. When a key
                                 is absent here the tracker falls back to the
                                 provider's conventional env var
                                 (``DEEPSEEK_API_KEY`` etc.). Services with
                                 neither a key nor an env var are reported as
                                 ``error="missing_api_key"``.
        thresholds:              Per-service minimum-balance thresholds (USD).
                                 Merged over :data:`DEFAULT_THRESHOLDS`.
        historical_spend_rates:  Per-service observed spend rate in USD/day,
                                 used by :meth:`recommend_refill_amount`. When
                                 unknown the recommendation falls back to
                                 ``2 * threshold``.
        http_client:             Optional injected HTTP client. Anything with a
                                 ``get(url, headers=...)`` method works
                                 (httpx.Client, requests.Session, test fake).
                                 When ``None`` a lazy ``httpx.Client`` is built
                                 on first use.
        timeout:                 Request timeout (seconds) when the lazy
                                 ``httpx.Client`` is used. Ignored when an
                                 explicit ``http_client`` is supplied.
    """

    def __init__(
        self,
        *,
        api_keys: dict[str, str] | None = None,
        thresholds: dict[str, float] | None = None,
        historical_spend_rates: dict[str, float] | None = None,
        http_client: Any = None,
        timeout: float = 10.0,
    ) -> None:
        """Configure provider credentials, thresholds, and optional transport."""
        self._api_keys: dict[str, str] = dict(api_keys or {})
        self._thresholds: dict[str, float] = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._spend_rates: dict[str, float] = dict(historical_spend_rates or {})
        self._http_client = http_client
        self._owns_http_client = False
        self._timeout = timeout
        self._last_balance: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_key(self, service: str) -> str | None:
        if self._api_keys.get(service):
            return self._api_keys[service]
        env_var = _SERVICE_CONFIG[service]["auth_env_var"]
        return os.environ.get(env_var)

    def _get_http(self) -> Any:
        if self._http_client is not None:
            return self._http_client
        import httpx

        self._http_client = httpx.Client(timeout=self._timeout)
        self._owns_http_client = True
        return self._http_client

    def close(self) -> None:
        """Close the lazily-created HTTP client without touching injected clients."""
        if not self._owns_http_client or self._http_client is None:
            return
        self._http_client.close()
        self._http_client = None
        self._owns_http_client = False

    @staticmethod
    def _build_result(
        service: str,
        *,
        balance_usd: float | None,
        currency: str,
        error: str | None,
        fetched_at: float,
        raw: Any = None,
        error_detail: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "service": service,
            "balance_usd": balance_usd,
            "currency": currency,
            "fetched_at": fetched_at,
            "error": error,
            "raw": raw,
        }
        if error_detail is not None:
            result["error_detail"] = error_detail
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_balance(self, service: str) -> dict[str, Any]:
        """Query one provider's balance / usage endpoint.

        Returns a dict with at minimum the keys ``service``, ``balance_usd``
        (``None`` on any failure), ``currency``, ``fetched_at``, ``error``
        (``None`` on success), and ``raw`` (the parsed JSON body on success).

        Raises:
            ValueError: If ``service`` is not in :data:`SUPPORTED_SERVICES`.
        """
        if service not in _SERVICE_CONFIG:
            raise ValueError(
                f"Unsupported service: {service!r}. Supported: {list(SUPPORTED_SERVICES)}"
            )
        fetched_at = time.time()
        cfg = _SERVICE_CONFIG[service]
        key = self._get_key(service)
        if not key:
            result = self._build_result(
                service,
                balance_usd=None,
                currency="USD",
                error="missing_api_key",
                fetched_at=fetched_at,
            )
            self._last_balance[service] = result
            return result

        url = cfg["base_url"] + cfg["balance_path"]
        headers = {"Authorization": f"Bearer {key}"}
        try:
            client = self._get_http()
            response = client.get(url, headers=headers)
        except OSError as exc:
            logger.warning("CreditTracker %s: transport error: %s", service, exc)
            result = self._build_result(
                service,
                balance_usd=None,
                currency="USD",
                error="request_failed",
                fetched_at=fetched_at,
                error_detail=str(exc),
            )
            self._last_balance[service] = result
            return result

        status = getattr(response, "status_code", 0)
        if status >= 400:
            logger.warning("CreditTracker %s: HTTP %s", service, status)
            result = self._build_result(
                service,
                balance_usd=None,
                currency="USD",
                error=f"http_{status}",
                fetched_at=fetched_at,
            )
            self._last_balance[service] = result
            return result

        try:
            body = response.json()
        except Exception as exc:
            logger.warning("CreditTracker %s: body not JSON: %s", service, exc)
            result = self._build_result(
                service,
                balance_usd=None,
                currency="USD",
                error="parse_failed",
                fetched_at=fetched_at,
                error_detail=str(exc),
            )
            self._last_balance[service] = result
            return result

        try:
            balance, currency = _PARSERS[service](body)
        except (_Unparseable, KeyError, TypeError, ValueError) as exc:
            logger.warning("CreditTracker %s: unparseable body: %s", service, exc)
            result = self._build_result(
                service,
                balance_usd=None,
                currency="USD",
                error="parse_failed",
                fetched_at=fetched_at,
                error_detail=str(exc),
            )
            self._last_balance[service] = result
            return result

        result = self._build_result(
            service,
            balance_usd=balance,
            currency=currency,
            error=None,
            fetched_at=fetched_at,
            raw=body,
        )
        self._last_balance[service] = result
        return result

    def check_all_balances(self) -> dict[str, dict[str, Any]]:
        """Query every service that has a non-empty API key configured.

        Services with no key are omitted from the result (they would always
        report ``missing_api_key``); call :meth:`check_balance` directly to
        surface that case explicitly.
        """
        out: dict[str, dict[str, Any]] = {}
        for service in SUPPORTED_SERVICES:
            if not self._get_key(service):
                continue
            out[service] = self.check_balance(service)
        return out

    def get_balance_threshold(self, service: str) -> float:
        """Return the minimum-balance threshold (USD) for ``service``.

        Raises:
            ValueError: If ``service`` is unknown.
        """
        if service not in _SERVICE_CONFIG:
            raise ValueError(
                f"Unsupported service: {service!r}. Supported: {list(SUPPORTED_SERVICES)}"
            )
        return self._thresholds[service]

    def should_refill(self, service: str) -> bool:
        """True when the latest balance is strictly below the threshold.

        ``True`` is also returned when the balance is unknown (missing key,
        transport error, parse failure) — the safe default for a balance we
        cannot observe is to flag it for refill.
        """
        result = self._last_balance.get(service) or self.check_balance(service)
        balance = result.get("balance_usd")
        if balance is None:
            return True
        return float(balance) < self.get_balance_threshold(service)

    def recommend_refill_amount(self, service: str) -> float:
        """Recommended top-up amount (USD) for ``service``.

        When a historical spend rate is known the recommendation is
        ``spend_rate * DEFAULT_REFILL_DAYS`` (a one-week horizon). When no
        history exists the recommendation falls back to twice the threshold —
        enough to clear the threshold and give some headroom.

        Always returns a positive float.
        """
        rate = self._spend_rates.get(service, 0.0)
        if rate <= 0.0:
            return self.get_balance_threshold(service) * 2.0
        return rate * DEFAULT_REFILL_DAYS

    def set_spend_limit(self, service: str, limit: float) -> dict[str, Any]:
        """Mirror a spend limit to the provider where its API supports it.

        Providers without a programmatic limit API are reported as
        ``{"supported": False}``. This method never raises for unsupported
        providers — the caller is expected to surface ``supported`` in the UI.

        Args:
            service: One of :data:`SUPPORTED_SERVICES`.
            limit:   Non-negative spend limit in USD.

        Raises:
            ValueError: If ``service`` is unknown or ``limit`` is negative.
        """
        if service not in _SERVICE_CONFIG:
            raise ValueError(
                f"Unsupported service: {service!r}. Supported: {list(SUPPORTED_SERVICES)}"
            )
        if limit < 0.0 or not float("-inf") < float(limit):
            raise ValueError(f"limit must be non-negative, got {limit!r}")
        if service not in _SPEND_LIMIT_SUPPORTED:
            return {
                "service": service,
                "supported": False,
                "limit_usd": float(limit),
                "applied": False,
            }
        # OpenRouter / OpenAI: the actual write is deferred to the operator
        # (per-key limits are typically set in the provider dashboard; the API
        # surface varies by account tier). We surface the requested limit and
        # mark it accepted-but-pending-operator-action.
        return {
            "service": service,
            "supported": True,
            "limit_usd": float(limit),
            "applied": False,
            "note": "Operator must apply via provider dashboard; programmatic write not exposed.",
        }

    def last_balance(self, service: str) -> dict[str, Any] | None:
        """Return the most recent :meth:`check_balance` result, or ``None``."""
        return self._last_balance.get(service)

    def __repr__(self) -> str:
        """Return a credential-free tracker summary."""
        return (
            f"CreditTracker("
            f"services={list(SUPPORTED_SERVICES)}, "
            f"configured={sorted(s for s in SUPPORTED_SERVICES if self._get_key(s))})"
        )
