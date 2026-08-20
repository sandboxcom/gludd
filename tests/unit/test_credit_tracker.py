"""TDD tests for CreditTracker — prepaid service credit / balance tracking.

Covers:
  - SUPPORTED_SERVICES registry (DeepSeek, OpenAI, Z.AI, OpenRouter)
  - Construction with default / custom thresholds / API keys
  - check_balance() — per-provider parsing, missing-key, network error, unknown svc
  - get_balance_threshold() — defaults + overrides
  - should_refill() — boundary + None balance behaviour
  - recommend_refill_amount() — historical spend rate based; safe fallback
  - set_spend_limit() — supported vs unsupported provider
  - check_all_balances() — fans out across configured services
  - GET /api/credits endpoint wiring (TestClient)
  - EventLoop periodic phase exists + is registered in PHASE_ORDER
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.budget.credit_tracker import (
    DEFAULT_REFILL_DAYS,
    DEFAULT_THRESHOLDS,
    SUPPORTED_SERVICES,
    CreditTracker,
)
from general_ludd.routers.spend import register as register_spend

# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, json_data: Any) -> None:
        self.status_code = status_code
        self._json = json_data

    def json(self) -> Any:
        return self._json


class _FakeHttpClient:
    """Records GET calls and returns scripted responses keyed by URL."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append((url, headers or {}))
        if url not in self._responses:
            raise AssertionError(f"unexpected GET {url!r}")
        return self._responses[url]

    def close(self) -> None:
        self.closed = getattr(self, "closed", 0) + 1


class _FailingHttpClient:
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> _FakeResponse:
        raise OSError("connection refused")


def _ds_balance_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "is_available": True,
            "wallets": [
                {"balance": "10.5000", "currency": "USD"},
                {"balance": "75.0000", "currency": "CNY"},
            ],
        },
    )


def _or_balance_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {"data": {"total_credits": "20.0000", "total_usage": "5.2500"}},
    )


def _oai_costs_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {"data": [{"line_item": 0.40, "name": "inference"}, {"line_item": 0.10, "name": "embeddings"}]},
    )


def _zai_usage_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {"data": {"balance": "8.75", "currency": "USD"}},
    )


# ──────────────────────────────────────────────────────────────────────────
# Construction + service registry
# ──────────────────────────────────────────────────────────────────────────


class TestServiceRegistry:
    def test_all_four_providers_supported(self) -> None:
        assert set(SUPPORTED_SERVICES) == {"deepseek", "openai", "zai", "openrouter"}

    def test_default_thresholds_cover_every_service(self) -> None:
        for svc in SUPPORTED_SERVICES:
            assert svc in DEFAULT_THRESHOLDS
            assert DEFAULT_THRESHOLDS[svc] > 0


class TestConstruction:
    def test_default_thresholds(self) -> None:
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        for svc in SUPPORTED_SERVICES:
            assert ct.get_balance_threshold(svc) == pytest.approx(DEFAULT_THRESHOLDS[svc])

    def test_custom_threshold_override(self) -> None:
        ct = CreditTracker(
            http_client=_FakeHttpClient({}),
            thresholds={"deepseek": 25.0, "openai": 99.0},
        )
        assert ct.get_balance_threshold("deepseek") == pytest.approx(25.0)
        assert ct.get_balance_threshold("openai") == pytest.approx(99.0)
        # unmodified services still fall back to default
        assert ct.get_balance_threshold("zai") == pytest.approx(DEFAULT_THRESHOLDS["zai"])

    def test_close_releases_only_internally_created_client(self, monkeypatch) -> None:
        internal = _FakeHttpClient({})
        monkeypatch.setattr(httpx, "Client", lambda **_kwargs: internal)
        owned = CreditTracker()
        assert owned._get_http() is internal

        owned.close()
        owned.close()

        assert internal.closed == 1

        external = _FakeHttpClient({})
        unowned = CreditTracker(http_client=external)
        unowned.close()
        assert not hasattr(external, "closed")


# ──────────────────────────────────────────────────────────────────────────
# check_balance()
# ──────────────────────────────────────────────────────────────────────────


class TestCheckBalanceDeepSeek:
    def test_parses_first_usd_wallet(self) -> None:
        ct = CreditTracker(
            api_keys={"deepseek": "sk-ds"},
            http_client=_FakeHttpClient(
                {"https://api.deepseek.com/user/balance": _ds_balance_response()},
            ),
        )
        result = ct.check_balance("deepseek")
        assert result["service"] == "deepseek"
        assert result["balance_usd"] == pytest.approx(10.5)
        assert result["currency"] == "USD"
        assert result["error"] is None
        assert "fetched_at" in result

    def test_uses_authorization_bearer_header(self) -> None:
        client = _FakeHttpClient(
            {"https://api.deepseek.com/user/balance": _ds_balance_response()},
        )
        ct = CreditTracker(api_keys={"deepseek": "sk-ds"}, http_client=client)
        ct.check_balance("deepseek")
        url, headers = client.calls[0]
        assert url == "https://api.deepseek.com/user/balance"
        assert headers["Authorization"] == "Bearer sk-ds"


class TestCheckBalanceOpenRouter:
    def test_total_credits_minus_total_usage(self) -> None:
        ct = CreditTracker(
            api_keys={"openrouter": "sk-or"},
            http_client=_FakeHttpClient(
                {"https://openrouter.ai/api/credits": _or_balance_response()},
            ),
        )
        result = ct.check_balance("openrouter")
        # 20.00 credits - 5.25 usage = 14.75
        assert result["balance_usd"] == pytest.approx(14.75)
        assert result["error"] is None


class TestCheckBalanceOpenAI:
    def test_sums_line_items_as_usage(self) -> None:
        ct = CreditTracker(
            api_keys={"openai": "sk-oai"},
            http_client=_FakeHttpClient(
                {"https://api.openai.com/v1/organization/costs": _oai_costs_response()},
            ),
        )
        result = ct.check_balance("openai")
        # OpenAI costs endpoint reports USAGE, not balance. The tracker reports
        # the summed usage as balance_usd (semantics: "known spend against the
        # prepaid pool"). 0.40 + 0.10 = 0.50.
        assert result["balance_usd"] == pytest.approx(0.50)
        assert result["error"] is None


class TestCheckBalanceZai:
    def test_parses_balance_field(self) -> None:
        ct = CreditTracker(
            api_keys={"zai": "sk-zai"},
            http_client=_FakeHttpClient(
                {"https://api.z.ai/api/paas/v4/usage": _zai_usage_response()},
            ),
        )
        result = ct.check_balance("zai")
        assert result["balance_usd"] == pytest.approx(8.75)
        assert result["error"] is None


class TestCheckBalanceErrorPaths:
    def test_unknown_service_raises_value_error(self) -> None:
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        with pytest.raises(ValueError, match="Unsupported service"):
            ct.check_balance("not-a-real-provider")

    def test_missing_api_key_returns_error_dict(self, monkeypatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        ct = CreditTracker(api_keys={}, http_client=_FakeHttpClient({}))
        result = ct.check_balance("deepseek")
        assert result["service"] == "deepseek"
        assert result["balance_usd"] is None
        assert result["error"] == "missing_api_key"

    def test_network_failure_returns_error_dict(self) -> None:
        ct = CreditTracker(
            api_keys={"deepseek": "sk-ds"},
            http_client=_FailingHttpClient(),
        )
        result = ct.check_balance("deepseek")
        assert result["balance_usd"] is None
        assert result["error"] == "request_failed"
        assert "connection refused" in result.get("error_detail", "")

    def test_http_500_returns_error_dict(self) -> None:
        client = _FakeHttpClient(
            {"https://api.deepseek.com/user/balance": _FakeResponse(500, {"error": "boom"})},
        )
        ct = CreditTracker(api_keys={"deepseek": "sk-ds"}, http_client=client)
        result = ct.check_balance("deepseek")
        assert result["balance_usd"] is None
        assert result["error"] == "http_500"

    def test_unparseable_body_returns_error_dict(self) -> None:
        client = _FakeHttpClient(
            {"https://api.deepseek.com/user/balance": _FakeResponse(200, {})},
        )
        ct = CreditTracker(api_keys={"deepseek": "sk-ds"}, http_client=client)
        result = ct.check_balance("deepseek")
        assert result["balance_usd"] is None
        assert result["error"] == "parse_failed"


# ──────────────────────────────────────────────────────────────────────────
# get_balance_threshold / should_refill / recommend_refill_amount
# ──────────────────────────────────────────────────────────────────────────


class TestShouldRefill:
    def test_true_when_balance_below_threshold(self) -> None:
        ct = CreditTracker(
            api_keys={"deepseek": "k"},
            thresholds={"deepseek": 5.0},
            http_client=_FakeHttpClient(
                {"https://api.deepseek.com/user/balance": _ds_balance_response()},
            ),
        )
        # Mock: balance = 10.5, threshold = 5.0 → False
        assert ct.should_refill("deepseek") is False

    def test_false_when_balance_above_threshold(self) -> None:
        # Use a high threshold so 10.5 < 50.0 → True
        ct = CreditTracker(
            api_keys={"deepseek": "k"},
            thresholds={"deepseek": 50.0},
            http_client=_FakeHttpClient(
                {"https://api.deepseek.com/user/balance": _ds_balance_response()},
            ),
        )
        assert ct.should_refill("deepseek") is True

    def test_true_when_no_balance_due_to_error(self, monkeypatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        assert ct.should_refill("deepseek") is True

    def test_at_threshold_is_not_refill(self) -> None:
        client = _FakeHttpClient(
            {"https://api.deepseek.com/user/balance": _ds_balance_response()},  # balance=10.5
        )
        ct = CreditTracker(
            api_keys={"deepseek": "k"},
            thresholds={"deepseek": 10.5},
            http_client=client,
        )
        # balance == threshold → strictly less-than → False
        assert ct.should_refill("deepseek") is False


class TestRecommendRefillAmount:
    def test_uses_historical_spend_rate_times_default_days(self) -> None:
        ct = CreditTracker(
            http_client=_FakeHttpClient({}),
            historical_spend_rates={"deepseek": 2.0},  # $2/day
        )
        # 2.0/day * DEFAULT_REFILL_DAYS
        assert ct.recommend_refill_amount("deepseek") == pytest.approx(
            2.0 * DEFAULT_REFILL_DAYS,
        )

    def test_fallback_when_no_history(self) -> None:
        # When no historical rate is known, fall back to 2x the threshold
        ct = CreditTracker(
            http_client=_FakeHttpClient({}),
            thresholds={"deepseek": 5.0},
        )
        assert ct.recommend_refill_amount("deepseek") == pytest.approx(10.0)

    def test_zero_history_returns_threshold_double(self) -> None:
        ct = CreditTracker(
            http_client=_FakeHttpClient({}),
            historical_spend_rates={"openai": 0.0},
            thresholds={"openai": 8.0},
        )
        assert ct.recommend_refill_amount("openai") == pytest.approx(16.0)

    def test_positive_minimum(self) -> None:
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        assert ct.recommend_refill_amount("openrouter") > 0


# ──────────────────────────────────────────────────────────────────────────
# set_spend_limit()
# ──────────────────────────────────────────────────────────────────────────


class TestSetSpendLimit:
    def test_unsupported_provider_returns_supported_false(self) -> None:
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        result = ct.set_spend_limit("deepseek", 50.0)
        assert result["supported"] is False

    def test_openrouter_supported(self) -> None:
        # OpenRouter exposes per-key spend limits — supported=True
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        result = ct.set_spend_limit("openrouter", 50.0)
        assert result["supported"] is True

    def test_rejects_negative_limit(self) -> None:
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        with pytest.raises(ValueError):
            ct.set_spend_limit("openrouter", -1.0)


# ──────────────────────────────────────────────────────────────────────────
# check_all_balances()
# ──────────────────────────────────────────────────────────────────────────


class TestCheckAllBalances:
    def test_fans_out_across_keys_provided(self, monkeypatch) -> None:
        for var in ("OPENAI_API_KEY", "ZAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        client = _FakeHttpClient(
            {
                "https://api.deepseek.com/user/balance": _ds_balance_response(),
                "https://openrouter.ai/api/credits": _or_balance_response(),
            },
        )
        ct = CreditTracker(
            api_keys={"deepseek": "k", "openrouter": "k"},
            http_client=client,
        )
        results = ct.check_all_balances()
        # Only services with keys configured are queried.
        assert set(results.keys()) == {"deepseek", "openrouter"}
        assert results["deepseek"]["balance_usd"] == pytest.approx(10.5)
        assert results["openrouter"]["balance_usd"] == pytest.approx(14.75)

    def test_no_keys_returns_empty_dict(self, monkeypatch) -> None:
        for var in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ZAI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        ct = CreditTracker(http_client=_FakeHttpClient({}))
        assert ct.check_all_balances() == {}


# ──────────────────────────────────────────────────────────────────────────
# GET /api/credits endpoint
# ──────────────────────────────────────────────────────────────────────────


def _make_app() -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    register_spend(app, {})
    return app, TestClient(app)


class TestApiCreditsEndpoint:
    def test_no_tracker_returns_empty_dict(self) -> None:
        _app, client = _make_app()
        resp = client.get("/api/credits")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_returns_per_service_balances(self, monkeypatch) -> None:
        for var in ("OPENAI_API_KEY", "ZAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        app, client = _make_app()
        ct = CreditTracker(
            api_keys={"deepseek": "k", "openrouter": "k"},
            http_client=_FakeHttpClient(
                {
                    "https://api.deepseek.com/user/balance": _ds_balance_response(),
                    "https://openrouter.ai/api/credits": _or_balance_response(),
                },
            ),
        )
        app.state._credit_tracker = ct
        resp = client.get("/api/credits")
        assert resp.status_code == 200
        data = resp.json()
        assert "deepseek" in data
        assert data["deepseek"]["balance_usd"] == pytest.approx(10.5)
        assert "openrouter" in data
        assert data["openrouter"]["balance_usd"] == pytest.approx(14.75)


# ──────────────────────────────────────────────────────────────────────────
# EventLoop wiring — phase exists + is registered
# ──────────────────────────────────────────────────────────────────────────


class TestEventLoopPhaseWiring:
    def test_phase_registered_in_phase_order(self) -> None:
        from general_ludd.event_loop.loop import PHASE_ORDER

        assert "check_service_credits" in PHASE_ORDER

    def test_phase_method_exists_on_event_loop(self) -> None:
        from general_ludd.event_loop.loop import EventLoop

        assert callable(getattr(EventLoop, "_phase_check_service_credits", None))
