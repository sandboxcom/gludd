"""Small models module — download, local inference, and eval harness."""

from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadProgress,
    DownloadSource,
    ModelDownloader,
)
from general_ludd.small_models.eval_harness import (
    EleutherAIHarness,
    EvalTask,
    HarnessConfig,
    ParsedResult,
    STANDARD_TASKS,
    parse_lm_eval_output,
    result_to_evidence,
    score_passing,
)
from general_ludd.small_models.radar_profile import (
    ModelRadarProfile,
    best_for_task,
    compare_models,
    generate_radar,
    render_radar_svg,
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
    "ModelRadarProfile",
    "ParsedResult",
    "best_for_task",
    "compare_models",
    "generate_radar",
    "parse_lm_eval_output",
    "render_radar_svg",
    "result_to_evidence",
    "score_passing",
]
