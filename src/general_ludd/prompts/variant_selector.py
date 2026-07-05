"""Prompt A/B variant selector for dispatch-path experimentation.

When prompted A/B testing is enabled, each dispatch alternates between
variant "A" and "B" based on a monotonic counter, so roughly half
of runs use each variant. The selected variant is recorded alongside the
template content hash so the variant→hash mapping is recoverable for
post-hoc analysis.

G6 auto-promotion: when a ``VariantMetrics`` instance is wired in, the
selector switches from round-robin to winner-only once enough samples
have been collected (default 10 per variant) and a clear winner emerges
via success rate (or latency as tie-breaker).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.prompts.variant_metrics import VariantMetrics


class PromptVariantSelector:
    """Selects prompt variant (A or B) on each call and returns metadata.

    Usage::

        selector = PromptVariantSelector()
        result = selector.select("dispatch_started")
        # result = {"variant": "A", "run_index": 0, "template_hash": "abc..."}
    """

    def __init__(
        self,
        template_hash: str | None = None,
        enabled: bool = True,
        variant_metrics: VariantMetrics | None = None,
    ) -> None:
        self._enabled = enabled
        self._template_hash: str | None = template_hash
        self._run_index: int = 0
        self._name_a: str = "A"
        self._name_b: str = "B"
        self._variant_metrics = variant_metrics
        self._last_template_name: str | None = None
        self._last_variant: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def template_hash(self) -> str | None:
        return self._template_hash

    @template_hash.setter
    def template_hash(self, value: str | None) -> None:
        self._template_hash = value

    @property
    def variant_metrics(self) -> VariantMetrics | None:
        return self._variant_metrics

    @variant_metrics.setter
    def variant_metrics(self, value: VariantMetrics | None) -> None:
        self._variant_metrics = value

    def select(self, template_name: str | None = None) -> dict[str, Any] | None:
        """Select the next variant and return metadata.

        When ``variant_metrics`` is set and a template has been promoted,
        the promoted variant is always returned (winner-only mode).
        Otherwise round-robin alternation is used.

        Returns None when ``enabled`` is False — the caller skips A/B
        recording entirely. Otherwise returns a dict with:
          - ``variant``: "A" or "B"
          - ``run_index``: zero-based dispatch counter
          - ``template_hash``: the current template content hash (if known)
          - ``template_name``: the name passed by the caller (if any)
        """
        if not self._enabled:
            return None

        promoted = None
        if self._variant_metrics is not None and template_name is not None:
            promoted = self._variant_metrics.is_promoted(template_name)
            if promoted is None:
                promoted = self._variant_metrics.get_winner(template_name)
                if promoted is not None:
                    self._variant_metrics.promote_winner(template_name)

        variant = promoted if promoted is not None else self._name_a if self._run_index % 2 == 0 else self._name_b

        result: dict[str, Any] = {
            "variant": variant,
            "run_index": self._run_index,
        }
        if self._template_hash is not None:
            result["template_hash"] = self._template_hash
        if template_name is not None:
            result["template_name"] = template_name

        self._last_template_name = template_name
        self._last_variant = variant
        self._run_index += 1
        return result

    def record_outcome(self, success: bool, latency_ms: float) -> None:
        """Record the outcome of the most recent ``select()`` call.

        Delegates to ``variant_metrics.record_outcome()`` using the
        last-selected template name and variant. No-op when metrics is not
        wired or no prior ``select()`` was called.
        """
        if (
            self._variant_metrics is not None
            and self._last_template_name is not None
            and self._last_variant is not None
        ):
            self._variant_metrics.record_outcome(
                self._last_template_name,
                self._last_variant,
                success=success,
                latency_ms=latency_ms,
            )

    def current_run_index(self) -> int:
        return self._run_index
