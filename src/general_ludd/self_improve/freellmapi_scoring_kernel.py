"""Compatibility imports for the universal FreeLLMAPI scoring kernel.

The implementation belongs to :mod:`general_ludd.models`; self-improvement is
one consumer and retains this import path only for compatibility.
"""

from general_ludd.models.freellmapi_scoring_kernel import (
    FREELLMAPI_SCORING_BUNDLE_DIGEST,
    FREELLMAPI_SCORING_UPSTREAM_COMMIT,
    FreeLLMScoringBatchResult,
    FreeLLMScoringFactors,
    FreeLLMScoringFault,
    FreeLLMScoringInput,
    FreeLLMScoringKernel,
    FreeLLMScoringSource,
    FreeLLMScoringTrace,
)

__all__ = [
    "FREELLMAPI_SCORING_BUNDLE_DIGEST",
    "FREELLMAPI_SCORING_UPSTREAM_COMMIT",
    "FreeLLMScoringBatchResult",
    "FreeLLMScoringFactors",
    "FreeLLMScoringFault",
    "FreeLLMScoringInput",
    "FreeLLMScoringKernel",
    "FreeLLMScoringSource",
    "FreeLLMScoringTrace",
]
