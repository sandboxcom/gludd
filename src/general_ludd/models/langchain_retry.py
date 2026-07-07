"""LangChain-based retry/fallback alternative for ModelGateway.

Enabled via config flag ``model.use_langchain_retry: true``.

The models themselves still go through the gateway's ``call_model`` (preserving
circuit breaker, budget guard, SSRF, secrets, caching).  Only the retry and
fallback orchestration is delegated to LangChain primitives
(``with_retry()`` / ``with_fallbacks()``).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda

logger = logging.getLogger(__name__)


class LangChainRetryGateway:
    """Alternative retry/fallback path using LangChain primitives.

    Wraps individual profile invocations as ``Runnable``\u00a0s, applies
    ``with_retry()`` to the primary, and chains fallbacks with
    ``with_fallbacks()``.

    The models themselves are constructed by the gateway's ``_invoke_and_bill``
    (called through ``call_model``), which retains all existing guards: circuit
    breaker, budget, SSRF, secrets, and caching.

    Usage::

        lc = LangChainRetryGateway(gateway)
        chain = lc.build_chain("primary", ["fb1", "fb2"], retry_config={
            "stop_after_attempt": 3,
        })
        response = lc.call(messages, tools=None, config={}, context={})

    The existing ``call_model_with_retry`` + ``_walk_fallbacks`` remain the
    default path (``model.use_langchain_retry: false``).
    """

    def __init__(self, gateway: object) -> None:
        self._gateway = gateway
        self._chain: Runnable[object, object] | None = None

    def _make_profile_runnable(self, profile_id: str) -> Runnable[object, object]:
        """Build a Runnable that invokes the gateway for a single profile."""

        def _invoke_profile(input: dict[str, object]) -> object:
            gateway = cast(Any, self._gateway)
            return gateway.call_model(
                profile_id,
                input["messages"],
                tools=input.get("tools"),
                **cast(Any, input.get("_call_kwargs", {})),
            )

        return cast(Runnable[object, object], RunnableLambda(cast(Any, _invoke_profile)))

    def build_chain(
        self,
        primary_id: str,
        fallback_ids: list[str],
        retry_config: dict[str, object] | None = None,
    ) -> Runnable[object, object]:
        """Build a LangChain Runnable chain with retry + fallbacks.

        Args:
            primary_id: The primary model profile ID.
            fallback_ids: Ordered fallback profile IDs.
            retry_config: Dict forwarded to ``with_retry()``.  Recognised keys:

                * ``stop_after_attempt`` (default 3)
                * ``wait_exponential_jitter`` (default True)
                * ``retry_if_exception_type`` (default ``(Exception,)``)
                * ``exponential_jitter_params`` — dict with keys ``initial``,
                  ``max``, ``exp_base``, ``jitter``.  Passed directly to
                  ``tenacity.wait_exponential_jitter``.

        Returns:
            A Runnable whose ``.invoke(input)`` calls the primary with retry
            and falls back to the configured fallback profiles.
        """
        cfg: dict[str, Any] = cast(dict[str, Any], dict(retry_config or {}))
        stop_after_attempt: int = cast(Any, cfg.pop("stop_after_attempt", 3))
        wait_exponential_jitter: bool = cast(Any, cfg.pop("wait_exponential_jitter", True))
        retry_exc_types: tuple[type[BaseException], ...] = cast(
            Any,
            cfg.pop("retry_if_exception_type", (Exception,)),
        )
        exponential_jitter_params: Any = cfg.pop("exponential_jitter_params", None)

        primary = self._make_profile_runnable(primary_id)

        primary_with_retry: Runnable[object, object] = primary.with_retry(
            stop_after_attempt=stop_after_attempt,
            wait_exponential_jitter=wait_exponential_jitter,
            retry_if_exception_type=retry_exc_types,
            exponential_jitter_params=exponential_jitter_params,
        )

        if not fallback_ids:
            self._chain = primary_with_retry
            return self._chain

        fallbacks = [self._make_profile_runnable(fid) for fid in fallback_ids]
        self._chain = primary_with_retry.with_fallbacks(fallbacks)
        return self._chain

    def call(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, object]] | None = None,
        config: dict[str, object] | None = None,
        context: dict[str, object] | None = None,
    ) -> object:
        """Invoke the retry + fallback chain.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Optional tool/function definitions.
            config: LangChain RunnableConfig-compatible dict (max_tokens, etc.).
            context: Extra kwargs forwarded through to the gateway
                     (budget, project_id, etc.).

        Returns:
            ``ModelResponse`` from the first successful profile invocation.

        Raises:
            The last exception if all profiles (including fallbacks) fail.
        """
        if self._chain is None:
            raise RuntimeError(
                "No chain built — call build_chain() first"
            )
        input_dict: dict[str, object] = {
            "messages": messages,
            "tools": tools,
            "_call_kwargs": context or {},
        }
        return self._chain.invoke(input_dict, config=cast(RunnableConfig, config or {}))


_default_retry_config: dict[str, object] = {
    "stop_after_attempt": 3,
    "wait_exponential_jitter": True,
}
