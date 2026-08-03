"""Small models module — download, local inference, and eval harness."""

from general_ludd.small_models.benchmark_report import (
    BenchmarkReport,
    generate_report,
    render_report,
)
from general_ludd.small_models.cost import (
    compute_cost_score,
    estimate_download_cost,
    estimate_inference_cost,
    estimate_quantize_cost,
    is_off_peak,
    next_off_peak_window,
    should_defer_download,
)
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
    "BenchmarkReport",
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
    "compute_cost_score",
    "estimate_download_cost",
    "estimate_inference_cost",
    "estimate_quantize_cost",
    "generate_radar",
    "generate_report",
    "is_off_peak",
    "list_tasks_for_model",
    "next_off_peak_window",
    "parse_lm_eval_output",
    "recommend_model",
    "render_radar_svg",
    "render_report",
    "result_to_evidence",
    "run_benchmark",
    "score_passing",
    "should_defer_download",
    "to_capability_evidence",
]
