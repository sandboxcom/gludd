"""Dynamic Azure model discovery, admission, and empirical selection tests."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_deployment_metadata import (
    ModelDeploymentMetadataFailure,
    ModelDeploymentMetadataUnavailable,
)
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_prediction import CandidatePrediction
from general_ludd.self_improve.azure_model_selection import (
    AzureModelSelectionPolicy,
    AzureModelSelectionReason,
    discover_and_select_azure_model,
    model_selection_identity_digest,
    write_azure_model_selection,
)
from general_ludd.self_improve.azure_model_selection_types import (
    AzureModelSelectionPolicy as SplitAzureModelSelectionPolicy,
)
from general_ludd.self_improve.candidate_classification import (
    classify_candidate_task,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

IMAGE = "registry.example/vllm@sha256:" + "9" * 64


def test_selector_preserves_policy_public_compatibility() -> None:
    """Existing selection integrations retain the established policy import."""
    assert AzureModelSelectionPolicy is SplitAzureModelSelectionPolicy


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _metadata(
    model_id: str,
    *,
    revision: str,
    parameters: int,
    context: int = 32_768,
    license_id: str = "apache-2.0",
    tags: tuple[str, ...] = ("code", "text-generation", "vllm"),
    downloads: int = 100,
) -> ModelDeploymentMetadata:
    return ModelDeploymentMetadata(
        model_id=model_id,
        revision=revision,
        parameter_count=parameters,
        context_tokens=context,
        storage_bytes=parameters * 2,
        weight_bits=16,
        license_id=license_id,
        tags=tuple(sorted(tags)),
        pipeline_tag="text-generation",
        library_name="transformers",
        downloads=downloads,
    )


def _policy() -> AzureModelSelectionPolicy:
    return AzureModelSelectionPolicy(
        search_query="code",
        search_limit=20,
        allowed_publishers=("trusted",),
        allowed_licenses=("apache-2.0",),
        required_tags=("code", "vllm"),
        blocked_tags=("custom_code",),
        minimum_context_tokens=28_672,
        container_image=IMAGE,
        profile_capacities=(
            AzureProfileCapacity(T4_PROFILE, 1, 900_000),
            AzureProfileCapacity(A100_PROFILE, 1, 3_500_000),
        ),
        max_hourly_cost_microusd=4_000_000,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("search_query", "two words", "search_query"),
        ("search_limit", True, "search_limit"),
        ("allowed_publishers", (), "allowed_publishers"),
        ("required_tags", ("code", "code"), "required_tags"),
        ("blocked_tags", ("code",), "must not overlap"),
        ("container_image", "registry.example/vllm:latest", "immutable"),
        ("profile_capacities", (), "profile_capacities"),
        ("minimum_context_tokens", 0, "minimum_context_tokens"),
        ("infrastructure_failure_threshold", 0, "failure_threshold"),
        ("infrastructure_failure_ttl_seconds", 86_401, "ttl_seconds"),
    ),
)
def test_model_selection_policy_rejects_unbounded_or_ambiguous_inputs(
    field: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        replace(_policy(), **{field: value})


def test_model_selection_policy_rejects_duplicate_profile_identity() -> None:
    profile = _policy().profile_capacities[0]

    with pytest.raises(ValueError, match="profile_capacities"):
        replace(_policy(), profile_capacities=(profile, profile))


class _Registry:
    def __init__(self, models: tuple[ModelDeploymentMetadata, ...]) -> None:
        self.models = {model.model_id: model for model in models}
        self.searches: list[dict[str, object]] = []
        self.hydrated: list[str] = []

    def search(self, **kwargs: object) -> list[ModelSearchResult]:
        self.searches.append(dict(kwargs))
        return [
            ModelSearchResult(model_id=model.model_id, downloads=model.downloads)
            for model in reversed(tuple(self.models.values()))
        ]

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
        self.hydrated.append(model_id)
        return self.models[model_id]


def _attempt(
    model: ModelDeploymentMetadata,
    policy: AzureModelSelectionPolicy,
    *,
    accepted: bool,
) -> CandidateAttempt:
    classification = classify_candidate_task("Implement a public Python feature.")
    prediction = CandidatePrediction(
        candidate_identity_digest=model_selection_identity_digest(model, policy),
        provider=ModelCandidateProvider.AZURE_CONTAINER_APP,
        task_type=classification.task_type,
        task_kind=classification.task_kind,
        evaluation_stratum_digest=_digest("older-compatible-stratum"),
        prompt_protocol_digest=_digest("prompt"),
        evaluator_digest=_digest("evaluator"),
        sampling_digest=_digest("sampling"),
        privacy_policy_digest=_digest("privacy"),
        predicted_acceptance=0.5,
        predicted_latency_ms=10_000,
        predicted_input_tokens=100,
        predicted_output_tokens=100,
        predicted_cost_microusd=1_000,
    )
    return CandidateAttempt(
        prediction=prediction,
        outcome=(
            CandidateAttemptOutcome.ACCEPTED
            if accepted
            else CandidateAttemptOutcome.REJECTED
        ),
        evaluation_score=1.0 if accepted else 0.0,
        blocker_count=0 if accepted else 1,
        observed_latency_ms=10_000,
        observed_input_tokens=100,
        observed_output_tokens=100,
        observed_cost_microusd=1_000,
    )


def test_discovery_uses_categorical_query_and_selects_cheapest_untested_fit() -> None:
    small = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    large = _metadata(
        "trusted/large-coder",
        revision="b" * 40,
        parameters=16_000_000_000,
        downloads=10_000,
    )
    registry = _Registry((small, large))
    traces: list[dict[str, object]] = []
    task = "Implement customer-specific pricing logic that must never leave the repo."

    selected = discover_and_select_azure_model(
        registry,
        classify_candidate_task(task),
        _policy(),
        attempts=(),
        trace_sink=traces.append,
    )

    assert selected.model == small
    assert selected.workload_profile_type == T4_PROFILE.workload_profile_type
    assert selected.reason is AzureModelSelectionReason.LEAST_TESTED_CHALLENGER
    assert registry.searches == [
        {
            "query": "code",
            "tags": ["code", "vllm"],
            "sort": "downloads",
            "limit": 20,
            "author": "trusted",
        }
    ]
    assert task not in json.dumps(registry.searches)
    assert all("small-coder" not in json.dumps(trace) for trace in traces)
    assert traces[-1]["event"] == "SELF_IMPROVE_AZURE_MODEL_SELECTED"


def test_recent_profile_failure_uses_only_cost_approved_sufficient_alternative() -> None:
    """Operational evidence can oversize explicitly without naming a model/GPU."""
    model = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    traces: list[dict[str, object]] = []

    selected = discover_and_select_azure_model(
        _Registry((model,)),
        classify_candidate_task("Implement a public Python feature."),
        _policy(),
        attempts=(),
        unavailable_profile_types=frozenset({T4_PROFILE.workload_profile_type}),
        trace_sink=traces.append,
    )

    assert selected.workload_profile_type == A100_PROFILE.workload_profile_type
    assert selected.reason is AzureModelSelectionReason.OPERATIONAL_FAILOVER
    assert selected.hourly_cost_microusd == 3_500_000
    assert traces[-1]["reason"] == "operational_failover"


def test_operational_failover_never_bypasses_hardware_or_cost_policy() -> None:
    model = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )

    with pytest.raises(ValueError, match="no deployable Azure model"):
        discover_and_select_azure_model(
            _Registry((model,)),
            classify_candidate_task("Implement a public Python feature."),
            replace(_policy(), max_hourly_cost_microusd=1_000_000),
            attempts=(),
            unavailable_profile_types=frozenset(
                {T4_PROFILE.workload_profile_type}
            ),
        )


def test_discovery_queries_allowed_publishers_with_one_bounded_total_budget() -> None:
    first = _metadata(
        "trusted/first-coder",
        revision="c" * 40,
        parameters=3_000_000_000,
        downloads=20,
    )
    second = _metadata(
        "second/second-coder",
        revision="d" * 40,
        parameters=4_000_000_000,
        downloads=10,
    )
    registry = _Registry((first, second))
    traces: list[dict[str, object]] = []

    discover_and_select_azure_model(
        registry,
        classify_candidate_task("Implement a public Python feature."),
        replace(
            _policy(),
            allowed_publishers=("trusted", "second"),
            search_limit=5,
        ),
        attempts=(),
        trace_sink=traces.append,
    )

    assert registry.searches == [
        {
            "query": "code",
            "tags": ["code", "vllm"],
            "sort": "downloads",
            "limit": 5,
            "author": "trusted",
        },
        {
            "query": "code",
            "tags": ["code", "vllm"],
            "sort": "downloads",
            "limit": 5,
            "author": "second",
        },
    ]
    assert traces[0]["publisher_query_count"] == 2
    assert traces[0]["discovered_count"] == 2
    assert traces[0]["hydrated_count"] == 2
    assert traces[0]["rank_sampling"] == "publisher_spread"
    assert traces[0]["schema_version"] == 4


def test_discovery_samples_deep_publisher_ranks_with_bounded_hydration() -> None:
    """Popularity must not hide cheaper models from hardware-aware selection."""
    models_by_publisher = {
        publisher: tuple(
            _metadata(
                f"{publisher}/coder-{rank}",
                revision=f"{rank + offset:x}" * 40,
                parameters=(70_000_000_000 if rank < 3 else 3_000_000_000),
                downloads=10_000 - rank,
            )
            for rank in range(4)
        )
        for publisher, offset in (("trusted", 1), ("second", 5))
    }

    class _RankedRegistry(_Registry):
        def __init__(self) -> None:
            super().__init__(tuple(sum(models_by_publisher.values(), ())))

        def search(self, **kwargs: object) -> list[ModelSearchResult]:
            self.searches.append(dict(kwargs))
            publisher = str(kwargs["author"])
            limit = int(kwargs["limit"])
            return [
                ModelSearchResult(
                    model_id=model.model_id,
                    downloads=model.downloads,
                )
                for model in models_by_publisher[publisher][:limit]
            ]

    registry = _RankedRegistry()
    traces: list[dict[str, object]] = []

    selected = discover_and_select_azure_model(
        registry,
        classify_candidate_task("Implement a public Python feature."),
        replace(
            _policy(),
            allowed_publishers=("trusted", "second"),
            search_limit=4,
        ),
        attempts=(),
        trace_sink=traces.append,
    )

    assert selected.model.parameter_count == 3_000_000_000
    assert selected.workload_profile_type == T4_PROFILE.workload_profile_type
    assert [search["limit"] for search in registry.searches] == [4, 4]
    assert len(registry.hydrated) == 4
    assert any(model_id.endswith("coder-3") for model_id in registry.hydrated)
    assert traces[0]["discovered_count"] == 4
    assert traces[0]["hydrated_count"] == 4


def test_least_tested_challenger_rotates_then_empirical_quality_wins() -> None:
    small = _metadata(
        "trusted/small-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    large = _metadata(
        "trusted/large-coder",
        revision="b" * 40,
        parameters=16_000_000_000,
    )
    policy = _policy()
    classification = classify_candidate_task("Implement a public Python feature.")

    challenger = discover_and_select_azure_model(
        _Registry((small, large)),
        classification,
        policy,
        attempts=(_attempt(small, policy, accepted=True),),
    )
    assert challenger.model == large
    assert challenger.reason is AzureModelSelectionReason.LEAST_TESTED_CHALLENGER

    learned = discover_and_select_azure_model(
        _Registry((small, large)),
        classification,
        policy,
        attempts=(
            _attempt(small, policy, accepted=False),
            _attempt(small, policy, accepted=False),
            _attempt(large, policy, accepted=True),
            _attempt(large, policy, accepted=True),
        ),
    )
    assert learned.model == large
    assert learned.reason is AzureModelSelectionReason.EMPIRICAL_QUALITY
    assert learned.evaluated_trials == 2
    assert learned.accepted_trials == 2


@pytest.mark.parametrize(
    ("model", "rejection"),
    (
        (
            _metadata(
                "untrusted/coder",
                revision="1" * 40,
                parameters=3_000_000_000,
            ),
            "publisher_not_allowed",
        ),
        (
            _metadata(
                "trusted/wrong-license",
                revision="2" * 40,
                parameters=3_000_000_000,
                license_id="other",
            ),
            "license_not_allowed",
        ),
        (
            _metadata(
                "trusted/missing-tag",
                revision="3" * 40,
                parameters=3_000_000_000,
                tags=("code", "text-generation"),
            ),
            "required_tag_missing",
        ),
        (
            _metadata(
                "trusted/custom-code",
                revision="4" * 40,
                parameters=3_000_000_000,
                tags=("code", "custom_code", "text-generation", "vllm"),
            ),
            "blocked_tag",
        ),
        (
            replace(
                _metadata(
                    "trusted/wrong-pipeline",
                    revision="5" * 40,
                    parameters=3_000_000_000,
                ),
                pipeline_tag="text-classification",
            ),
            "pipeline_unsupported",
        ),
        (
            _metadata(
                "trusted/short-context",
                revision="6" * 40,
                parameters=3_000_000_000,
                context=8_192,
            ),
            "context_too_short",
        ),
        (
            _metadata(
                "trusted/too-large",
                revision="7" * 40,
                parameters=70_000_000_000,
            ),
            "hardware_unavailable",
        ),
    ),
)
def test_discovery_rejects_untrusted_or_unfitted_candidates(
    model: ModelDeploymentMetadata,
    rejection: str,
) -> None:
    traces: list[dict[str, object]] = []
    with pytest.raises(ValueError, match="no deployable Azure model"):
        discover_and_select_azure_model(
            _Registry((model,)),
            classify_candidate_task("Implement a public Python feature."),
            _policy(),
            attempts=(),
            trace_sink=traces.append,
        )

    assert traces == [
        {
            "event": "SELF_IMPROVE_AZURE_MODEL_DISCOVERY_COMPLETED",
            "discovered_count": 1,
            "eligible_count": 0,
            "hydrated_count": 1,
            "publisher_query_count": 1,
            "rank_sampling": "publisher_spread",
            "rejection_counts": {rejection: 1},
            "schema_version": 4,
        }
    ]


def test_discovery_reports_cost_and_metadata_rejections_without_model_ids() -> None:
    affordable = _metadata(
        "trusted/affordable",
        revision="8" * 40,
        parameters=3_000_000_000,
    )
    unavailable = _metadata(
        "trusted/unavailable",
        revision="9" * 40,
        parameters=3_000_000_000,
    )

    class _PartiallyUnavailableRegistry(_Registry):
        def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
            if model_id == unavailable.model_id:
                raise ModelDeploymentMetadataUnavailable(
                    ModelDeploymentMetadataFailure.CONTEXT,
                    "provider response included sensitive details",
                )
            return super().get_deployment_metadata(model_id)

    traces: list[dict[str, object]] = []
    with pytest.raises(ValueError, match="no deployable Azure model"):
        discover_and_select_azure_model(
            _PartiallyUnavailableRegistry((affordable, unavailable)),
            classify_candidate_task("Implement a public Python feature."),
            replace(_policy(), max_hourly_cost_microusd=800_000),
            attempts=(),
            trace_sink=traces.append,
        )

    assert traces[-1]["rejection_counts"] == {
        "hourly_cost_exceeded": 1,
        "metadata_context_unavailable": 1,
    }
    assert "trusted/" not in json.dumps(traces)
    assert "sensitive" not in json.dumps(traces)


def test_selection_writer_is_exclusive_private_and_exact(tmp_path: Path) -> None:
    model = _metadata(
        "trusted/discovered-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    selection = discover_and_select_azure_model(
        _Registry((model,)),
        classify_candidate_task("Implement a public Python feature."),
        _policy(),
        attempts=(),
    )
    output = tmp_path / "selection.json"

    write_azure_model_selection(output, selection)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert payload["model_id"] == model.model_id
    assert payload["revision"] == model.revision
    assert payload["selection_identity_digest"] == selection.identity_digest
    assert payload["selection_reason"] == "least_tested_challenger"
    assert payload["container_image"] == IMAGE
    assert [
        profile["workload_profile_type"] for profile in payload["profile_capacities"]
    ] == [
        A100_PROFILE.workload_profile_type,
        T4_PROFILE.workload_profile_type,
    ]
    assert "t4_hourly_cost_microusd" not in payload
    assert "a100_hourly_cost_microusd" not in payload
    with pytest.raises(ValueError, match="new private output"):
        write_azure_model_selection(output, selection)


def test_task_type_is_part_of_empirical_selection_scope() -> None:
    model = _metadata(
        "trusted/discovered-coder",
        revision="a" * 40,
        parameters=3_000_000_000,
    )
    unrelated = replace(
        _attempt(model, _policy(), accepted=False),
        prediction=replace(
            _attempt(model, _policy(), accepted=False).prediction,
            task_type=TaskType.DOCUMENTATION,
        ),
    )

    selected = discover_and_select_azure_model(
        _Registry((model,)),
        classify_candidate_task("Implement a public Python feature."),
        _policy(),
        attempts=(unrelated,),
    )

    assert selected.evaluated_trials == 0
