"""Hermetic workflow boundary for FreeLLMAPI self-improvement candidates.

This module exposes a single stable entry point that composes the universal
model-infrastructure gates (upstream admission, frozen-delta evaluation) into
one content-free review workflow.  It never admits runtime code and never
reflects task, prompt, or model content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from general_ludd.models.freellmapi_frozen_delta import (
    FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION,
    evaluate_frozen_delta,
)

FREELLMAPI_WORKFLOW_SCHEMA_VERSION = FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION


def evaluate_freellmapi_candidate(
    *,
    candidate_lock: Mapping[str, object],
    plan: Mapping[str, object],
    abi_manifest: Mapping[str, object],
    observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Run the frozen-delta review workflow for one admitted candidate.

    This is the canonical hermetic boundary used by self-improvement fake
    tests and offline evaluation pipelines.  The result is deterministic,
    content-free, and never runtime-admitting.
    """
    return evaluate_frozen_delta(
        candidate_lock=candidate_lock,
        plan=plan,
        abi_manifest=abi_manifest,
        observations=observations,
    )


__all__ = [
    "FREELLMAPI_WORKFLOW_SCHEMA_VERSION",
    "evaluate_freellmapi_candidate",
]
