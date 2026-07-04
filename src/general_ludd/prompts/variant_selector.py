"""Prompt A/B variant selector for dispatch-path experimentation.

When prompted A/B testing is enabled, each dispatch alternates between
variant "A" and variant "B" based on a monotonic counter, so roughly half
of runs use each variant. The selected variant is recorded alongside the
template content hash so the variant→hash mapping is recoverable for
post-hoc analysis.
"""

from __future__ import annotations

from typing import Any


class PromptVariantSelector:
    """Selects prompt variant (A or B) on each call and returns metadata.

    Usage:
        selector = PromptVariantSelector()
        result = selector.select("dispatch_started")
        # result = {"variant": "A", "run_index": 0, "template_hash": "abc..."}
    """

    def __init__(self, template_hash: str | None = None, enabled: bool = True) -> None:
        self._enabled = enabled
        self._template_hash: str | None = template_hash
        self._run_index: int = 0
        self._name_a: str = "A"
        self._name_b: str = "B"

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

    def select(self, template_name: str | None = None) -> dict[str, Any] | None:
        """Select the next variant and return metadata.

        Returns None when ``enabled`` is False — the caller skips A/B
        recording entirely. Otherwise returns a dict with:
          - ``variant``: "A" or "B"
          - ``run_index``: zero-based dispatch counter
          - ``template_hash``: the current template content hash (if known)
          - ``template_name``: the name passed by the caller (if any)
        """
        if not self._enabled:
            return None
        variant = self._name_a if (self._run_index % 2 == 0) else self._name_b
        result: dict[str, Any] = {
            "variant": variant,
            "run_index": self._run_index,
        }
        if self._template_hash is not None:
            result["template_hash"] = self._template_hash
        if template_name is not None:
            result["template_name"] = template_name
        self._run_index += 1
        return result

    def current_run_index(self) -> int:
        return self._run_index
