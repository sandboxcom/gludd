"""Provider-neutral model predictions, empirical calibration, and trial plans.

The public facade accepts no source text, paths, endpoints, or credentials.  Its
types therefore support local and Azure candidates without making project-private
material part of durable evidence or event payloads.
"""

from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_calibration import (
    CalibrationReport,
    CalibrationSkipReason,
    CalibrationUpdate,
    load_calibration_attempts,
    prequential_brier_skill,
    record_calibration_attempt,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve._candidate_trials import (
    CandidateRanking,
    CandidateTrial,
    CandidateTrialPlan,
    CandidateTrialPurpose,
    plan_bounded_candidate_trials,
    rank_candidate_predictions,
)
from general_ludd.self_improve.candidate_execution import (
    CandidateEvaluation,
    CandidateExecutionBoundary,
    CandidateExecutionError,
    CandidateExecutionEvent,
    CandidateExecutionResult,
    CandidateExecutionScopeFailure,
    CandidateExecutionTrace,
    CandidateTrialCall,
    CandidateTrialExecution,
    execute_candidate_trial_plan,
)

__all__ = (
    "CalibrationReport",
    "CalibrationSkipReason",
    "CalibrationUpdate",
    "CandidateAttempt",
    "CandidateAttemptOutcome",
    "CandidateEvaluation",
    "CandidateExecutionBoundary",
    "CandidateExecutionError",
    "CandidateExecutionEvent",
    "CandidateExecutionResult",
    "CandidateExecutionScopeFailure",
    "CandidateExecutionTrace",
    "CandidatePrediction",
    "CandidateRanking",
    "CandidateTrial",
    "CandidateTrialCall",
    "CandidateTrialExecution",
    "CandidateTrialPlan",
    "CandidateTrialPurpose",
    "execute_candidate_trial_plan",
    "load_calibration_attempts",
    "plan_bounded_candidate_trials",
    "prequential_brier_skill",
    "rank_candidate_predictions",
    "record_calibration_attempt",
)
