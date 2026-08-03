"""Small models module — download, local inference, and eval harness."""

from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadProgress,
    DownloadSource,
    ModelDownloader,
)
from general_ludd.small_models.eval_harness import (
    STANDARD_TASKS,
    EleutherAIHarness,
    EvalTask,
    HarnessConfig,
    ParsedResult,
    parse_lm_eval_output,
    result_to_evidence,
    score_passing,
)

__all__ = [
    "STANDARD_TASKS",
    "DownloadProgress",
    "DownloadSource",
    "DownloadedModel",
    "EleutherAIHarness",
    "EvalTask",
    "HarnessConfig",
    "ModelDownloader",
    "ParsedResult",
    "parse_lm_eval_output",
    "result_to_evidence",
    "score_passing",
]
