"""Token-cost capture at the gateway billing chokepoint.

The per-work_type token capture was RELOCATED from the daemon event-loop path
(which only saw the generation branch) into ``ModelGateway._invoke_and_bill`` —
the single path EVERY billed model call flows through (daemon, worker,
ToolCallLoop, reviewer, SLM, langgraph). These tests pin that behaviour:

* a billed call records exactly the provider's real (input, output) token counts
  under the BARE ``work_type`` key that ``environment_advisor.classify`` consumes;
* a cache hit (which returns before ``_invoke_and_bill``) records nothing;
* ``work_type`` is popped before the provider ctor, so it never leaks into the
  LangChain provider constructor (no ``TypeError``).
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

import httpx

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.observability import token_cost
from general_ludd.observability.token_cost import TokenCostTracker


class _FakeResponse:
    """Provider response exposing only .content and .usage_metadata.

    No ``tool_calls`` / ``response_metadata`` attributes, so the gateway treats
    it as a plain, cacheable, non-tool-call turn.
    """

    def __init__(self, usage: dict[str, int]) -> None:
        self.content = "hello"
        self.usage_metadata = usage


class _FakeProvider:
    """Records the ctor kwargs it received so the test can prove no leak.

    Accepts ``model`` plus ``**extra`` and stashes ``extra`` — if ``work_type``
    (or any other non-provider kwarg) leaked through ``init_kwargs`` it would show
    up here. A strict ctor would raise TypeError; recording lets us assert the
    exact leaked set is empty.
    """

    last_extra: ClassVar[dict[str, Any]] = {}
    usage: ClassVar[dict[str, int]] = {"input_tokens": 7, "output_tokens": 11}

    def __init__(self, *, model: str, **extra: Any) -> None:
        type(self).last_extra = dict(extra)
        self._model = model

    def invoke(self, messages: Any) -> _FakeResponse:
        return _FakeResponse(dict(type(self).usage))


class _FakeRegistry:
    """Minimal provider registry: always installed, returns _FakeProvider."""

    def is_installed(self, name: str) -> bool:
        return True

    def install_provider(self, name: str) -> None:  # pragma: no cover - unused
        pass

    def get_provider_class(self, name: str) -> type[_FakeProvider]:
        return _FakeProvider


class _DictCache:
    """In-memory response cache with the get/set shape the gateway calls."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(self, key: str, value: Any, expire: int | None = None) -> None:
        self.store[key] = value


def _make_gateway(*, response_cache: Any | None = None) -> ModelGateway:
    profile = ModelProfile(
        model_profile_id="p",
        enabled=True,
        provider="openai",
        model_name="gpt-4",
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
    )
    return ModelGateway(
        profiles=[profile],
        provider_registry=_FakeRegistry(),
        response_cache=response_cache,
    )


def test_billed_call_records_real_token_counts_under_work_type() -> None:
    tracker = TokenCostTracker(min_samples=1)
    _FakeProvider.usage = {"input_tokens": 7, "output_tokens": 11}
    with patch.object(token_cost, "_shared_tracker", tracker):
        gw = _make_gateway()
        resp = gw.call_model(
            "p", [{"role": "user", "content": "hi"}], work_type="code"
        )
    assert resp.content == "hello"
    w = tracker.weight("code")
    assert w is not None
    # Exactly the provider's real usage_metadata counts, keyed on bare work_type.
    assert w.samples == 1
    assert w.median_input == 7
    assert w.median_output == 11
    assert w.median_total == 18
    # Nothing recorded under any other key.
    assert tracker.weight("unknown") is None


def test_cache_hit_records_nothing() -> None:
    tracker = TokenCostTracker(min_samples=1)
    cache = _DictCache()
    messages = [{"role": "user", "content": "hi"}]
    with patch.object(token_cost, "_shared_tracker", tracker):
        gw = _make_gateway(response_cache=cache)
        gw.call_model("p", messages, work_type="code")  # miss -> billed -> records
        assert len(tracker._history["code"]) == 1
        # Identical call: served from cache before _invoke_and_bill, so no record.
        gw.call_model("p", messages, work_type="code")
        assert len(tracker._history["code"]) == 1


def test_work_type_not_forwarded_to_provider_ctor() -> None:
    tracker = TokenCostTracker(min_samples=1)
    _FakeProvider.last_extra = {"sentinel": "unset"}
    with patch.object(token_cost, "_shared_tracker", tracker):
        gw = _make_gateway()
        # Must not raise TypeError from a leaked work_type kwarg.
        gw.call_model("p", [{"role": "user", "content": "hi"}], work_type="code")
    # work_type was popped in _invoke_and_bill; only provider-safe kwargs
    # (request_timeout from C6 hardening) reach the ctor.
    assert "work_type" not in _FakeProvider.last_extra
    request_timeout = _FakeProvider.last_extra.pop("request_timeout", None)
    assert isinstance(request_timeout, httpx.Timeout)
    assert _FakeProvider.last_extra == {}


def test_default_work_type_used_when_unspecified() -> None:
    tracker = TokenCostTracker(min_samples=1)
    with patch.object(token_cost, "_shared_tracker", tracker):
        gw = _make_gateway()
        # No work_type kwarg -> defaults to the "unknown" bucket.
        gw.call_model("p", [{"role": "user", "content": "hi"}])
    assert tracker.weight("unknown") is not None
    assert tracker.weight("code") is None


def test_explicit_none_work_type_coerced_to_unknown() -> None:
    # The admin/langgraph path can pass work_type=None explicitly (key present),
    # which bypasses the pop() default. It must coerce to the "unknown" bucket
    # rather than record a dead None key that classify() can never resolve.
    tracker = TokenCostTracker(min_samples=1)
    with patch.object(token_cost, "_shared_tracker", tracker):
        gw = _make_gateway()
        gw.call_model("p", [{"role": "user", "content": "hi"}], work_type=None)
    assert None not in tracker._history
    assert tracker.weight("unknown") is not None
