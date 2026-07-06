"""LangSmith tracer — per-call observability side-channel for ModelGateway.

Wraps LangSmith tracing as an additive, non-blocking observability layer.
Never affects control flow — if LangSmith is unavailable, traces are no-ops.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class LangSmithTracer:
    """Emits per-model-call traces to LangSmith when configured.

    Enabled only when both LANGSMITH_API_KEY and LANGSMITH_PROJECT are set.
    Gracefully degrades: if LangSmith is not installed or is unreachable,
    trace calls are silent no-ops that never raise.
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("LANGSMITH_API_KEY", "")
        self._project = os.environ.get("LANGSMITH_PROJECT", "")
        self._enabled = bool(self._api_key) and bool(self._project)
        self._client: Any = None

        if self._enabled:
            try:
                __import__("langsmith")
                self._client = self._build_client()
            except ImportError:
                logger.debug(
                    "LangSmith SDK not installed; traces disabled"
                )
                self._enabled = False
            except Exception as exc:
                logger.debug(
                    "LangSmith client init failed (%s); traces disabled",
                    exc,
                )
                self._enabled = False

    def _build_client(self) -> Any:
        import langsmith

        return langsmith.Client(api_key=self._api_key)

    def is_enabled(self) -> bool:
        return self._enabled

    def trace_call(
        self,
        model_name: str,
        messages: list[dict[str, str]],
        response: str,
        tokens: dict[str, int],
        cost: float,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if not self._enabled or self._client is None:
            return

        try:
            metadata = dict(metadata or {})
            metadata.update({
                "model_name": model_name,
                "input_tokens": str(tokens.get("input", 0)),
                "output_tokens": str(tokens.get("output", 0)),
                "cost": f"{cost:.8f}",
            })

            trimmed_response = response[:2000] if response else ""
            trimmed_messages = [
                {"role": m.get("role", ""), "content": str(m.get("content", ""))[:500]}
                for m in messages
            ]

            self._client.create_run(
                name=f"{model_name} call",
                run_type="llm",
                project_name=self._project,
                inputs={"messages": trimmed_messages},
                outputs={"text": trimmed_response},
                extra={"metadata": metadata},
                tags=["model-gateway"],
            )
        except Exception as exc:
            logger.debug(
                "LangSmith trace_call failed (%s); continuing",
                exc,
            )
