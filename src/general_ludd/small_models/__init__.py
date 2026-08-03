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
from general_ludd.small_models.evidence_store import (
    CapabilityEvidenceStore,
)
from general_ludd.small_models.lm_eval_runner import (
    LMEvalRunner,
    run_benchmark,
    to_capability_evidence,
)
from general_ludd.small_models.radar_profile import (
    ModelRadarProfile,
    RadarProfile,
    best_for_task,
    build_profile,
    compare_models,
    generate_radar,
    render_radar_svg,
)
from general_ludd.small_models.recommender import (
    ModelRecommendation,
    list_tasks_for_model,
    recommend_model,
)

__all__ = [
    "STANDARD_TASKS",
    "CapabilityEvidenceStore",
    "DownloadProgress",
    "DownloadSource",
    "DownloadedModel",
    "EleutherAIHarness",
    "EvalTask",
    "HarnessConfig",
    "LMEvalRunner",
    "ModelDownloader",
    "ModelRadarProfile",
    "ModelRecommendation",
    "ParsedResult",
    "RadarProfile",
    "best_for_task",
    "build_profile",
    "compare_models",
    "generate_radar",
    "list_tasks_for_model",
    "parse_lm_eval_output",
    "recommend_model",
    "render_radar_svg",
    "result_to_evidence",
    "run_benchmark",
    "score_passing",
    "to_capability_evidence",
]
