from __future__ import annotations

import json
from enum import Enum

from general_ludd.agents.types import AgentType
from general_ludd.approval.gate import ApprovalDecision
from general_ludd.chemistry.schemas import (
    DataClassification,
    EntityKind,
    FractionBasis,
    IsotopeStatus,
    ResultStatus,
    RiskTier,
    StereoStatus,
    StructureRepresentation,
    TaskKind,
    ValidationStatus,
)
from general_ludd.cloud.lifecycle_validator import LifecyclePhase
from general_ludd.controllers.merge_conflict import ConflictKind, ResolutionStrategy
from general_ludd.git_release.contracts import HelperAuthority, ReleaseVerdictState
from general_ludd.git_release.deployment import DeploymentStrategy
from general_ludd.git_release.release_state import ReleaseState
from general_ludd.infra.cost_tracker import CloudProvider, ResourceType
from general_ludd.infra.deploy_strategy import DeployUrgency
from general_ludd.ipc.queue import OverflowPolicy
from general_ludd.reload.manager import ReloadType
from general_ludd.routing_roles.small_model_policy import (
    CompletionAction,
    DispatchAction,
    TaskImpact,
)
from general_ludd.security.adversarial_detector import Category, Severity
from general_ludd.security.db_telemetry import DiskPressureStatus
from general_ludd.security.session_ttl import SessionValidation
from general_ludd.ssl.algorithms import AlgorithmStatus, AlgorithmType
from general_ludd.ssl_agent.cert_manager import ComplianceProfile


def _unique_values(cls: type[Enum]) -> list[str]:
    return sorted(set(m.value for m in cls))


def _member_names(cls: type[Enum]) -> list[str]:
    return sorted(m.name for m in cls)


# ── test class 1: value uniqueness audit ────────────────────────────────


class TestEnumValueUniqueness:
    def test_approval_decision_no_duplicates(self) -> None:
        assert len(_unique_values(ApprovalDecision)) == len(list(ApprovalDecision))

    def test_compliance_profile_no_duplicates(self) -> None:
        assert len(_unique_values(ComplianceProfile)) == len(list(ComplianceProfile))

    def test_overflow_policy_no_duplicates(self) -> None:
        assert len(_unique_values(OverflowPolicy)) == len(list(OverflowPolicy))

    def test_reload_type_no_duplicates(self) -> None:
        assert len(_unique_values(ReloadType)) == len(list(ReloadType))

    def test_lifecycle_phase_no_duplicates(self) -> None:
        assert len(_unique_values(LifecyclePhase)) == len(list(LifecyclePhase))

    def test_deploy_urgency_no_duplicates(self) -> None:
        assert len(_unique_values(DeployUrgency)) == len(list(DeployUrgency))

    def test_algorithm_status_no_duplicates(self) -> None:
        assert len(_unique_values(AlgorithmStatus)) == len(list(AlgorithmStatus))

    def test_algorithm_type_no_duplicates(self) -> None:
        assert len(_unique_values(AlgorithmType)) == len(list(AlgorithmType))

    def test_disk_pressure_status_no_duplicates(self) -> None:
        assert len(_unique_values(DiskPressureStatus)) == len(list(DiskPressureStatus))

    def test_session_validation_no_duplicates(self) -> None:
        assert len(_unique_values(SessionValidation)) == len(list(SessionValidation))

    def test_agent_type_no_duplicates(self) -> None:
        assert len(_unique_values(AgentType)) == len(list(AgentType))

    def test_conflict_kind_no_duplicates(self) -> None:
        assert len(_unique_values(ConflictKind)) == len(list(ConflictKind))

    def test_resolution_strategy_no_duplicates(self) -> None:
        assert len(_unique_values(ResolutionStrategy)) == len(list(ResolutionStrategy))

    def test_task_impact_no_duplicates(self) -> None:
        assert len(_unique_values(TaskImpact)) == len(list(TaskImpact))

    def test_release_state_no_duplicates(self) -> None:
        assert len(_unique_values(ReleaseState)) == len(list(ReleaseState))

    def test_cloud_provider_no_duplicates(self) -> None:
        assert len(_unique_values(CloudProvider)) == len(list(CloudProvider))

    def test_entity_kind_no_duplicates(self) -> None:
        assert len(_unique_values(EntityKind)) == len(list(EntityKind))


# ── test class 2: serialization roundtrip ───────────────────────────────


class TestEnumSerializationRoundtrip:
    def test_approval_decision_roundtrip(self) -> None:
        for m in ApprovalDecision:
            assert ApprovalDecision(m.value) is m

    def test_lifecycle_phase_roundtrip(self) -> None:
        for m in LifecyclePhase:
            assert LifecyclePhase(m.value) is m

    def test_reload_type_roundtrip(self) -> None:
        for m in ReloadType:
            assert ReloadType(m.value) is m

    def test_algorithm_type_roundtrip(self) -> None:
        for m in AlgorithmType:
            assert AlgorithmType(m.value) is m

    def test_session_validation_roundtrip(self) -> None:
        for m in SessionValidation:
            assert SessionValidation(m.value) is m

    def test_release_state_roundtrip(self) -> None:
        for m in ReleaseState:
            assert ReleaseState(m.value) is m

    def test_cloud_provider_roundtrip(self) -> None:
        for m in CloudProvider:
            assert CloudProvider(m.value) is m

    def test_resource_type_roundtrip(self) -> None:
        for m in ResourceType:
            assert ResourceType(m.value) is m

    def test_deployment_strategy_roundtrip(self) -> None:
        for m in DeploymentStrategy:
            assert DeploymentStrategy(m.value) is m


# ── test class 3: from-string / from-value parsing ──────────────────────


class TestEnumFromStringParsing:
    def test_approval_decision_from_string(self) -> None:
        assert ApprovalDecision("approved") is ApprovalDecision.APPROVED
        assert ApprovalDecision("denied") is ApprovalDecision.DENIED
        assert ApprovalDecision("pending") is ApprovalDecision.PENDING

    def test_lifecycle_phase_from_string(self) -> None:
        for val in ("idle", "provisioning", "running", "destroying", "destroyed", "orphaned"):
            assert LifecyclePhase(val) is getattr(LifecyclePhase, val.upper())

    def test_reload_type_from_string(self) -> None:
        for val in ("config", "prompts", "rules", "worker_code", "event_loop_code", "schema_migration"):
            assert ReloadType(val) is ReloadType[val.upper()]

    def test_severity_from_string(self) -> None:
        for val in ("critical", "high", "medium", "low", "info"):
            assert Severity(val) is Severity[val.upper()]

    def test_category_from_string(self) -> None:
        for val in (
            "self_sabotage",
            "backdoor",
            "credential_leak",
            "logic_degrade",
            "dependency_attack",
            "obfuscation",
        ):
            assert Category(val) is Category[val.upper()]

    def test_task_impact_from_string(self) -> None:
        expected = (
            "read_source",
            "write_artifact",
            "mutate_repository",
            "execute_command",
            "network_write",
            "credential_access",
            "deployment",
            "release",
            "security_decision",
        )
        for val in expected:
            assert TaskImpact(val) is TaskImpact[val.upper()]

    def test_release_state_from_string(self) -> None:
        for val in (
            "discover",
            "plan",
            "build_once",
            "verify_offline",
            "stage",
            "canary",
            "promote",
            "verify_release_page",
            "released",
            "rollback",
        ):
            assert ReleaseState(val) is ReleaseState[val.upper()]


# ── test class 4: JSON serialization ────────────────────────────────────


class TestEnumJsonSerialization:
    def test_cloud_provider_json_roundtrip(self) -> None:
        body = {"provider": CloudProvider.AZURE, "tier": "standard"}
        raw = json.dumps(body, default=str)
        back = json.loads(raw)
        assert back["provider"] == "azure"

    def test_risk_tier_json_roundtrip(self) -> None:
        body = {"tier": RiskTier.high}
        raw = json.dumps(body, default=str)
        back = json.loads(raw)
        assert back["tier"] == "high"

    def test_task_kind_json_inventory(self) -> None:
        body = {"kind": TaskKind.inventory}
        raw = json.dumps(body, default=str)
        back = json.loads(raw)
        assert back["kind"] == "inventory"

    def test_validation_status_json_warning(self) -> None:
        body = {"status": ValidationStatus.warning}
        raw = json.dumps(body, default=str)
        back = json.loads(raw)
        assert back["status"] == "warning"


# ── test class 5: member counts (regression guard) ──────────────────────


class TestEnumMemberCounts:
    def test_lifecycle_phase_has_six_members(self) -> None:
        assert len(list(LifecyclePhase)) == 6

    def test_reload_type_has_six_members(self) -> None:
        assert len(list(ReloadType)) == 6

    def test_algorithm_type_has_seven_members(self) -> None:
        assert len(list(AlgorithmType)) == 7

    def test_session_validation_has_five_members(self) -> None:
        assert len(list(SessionValidation)) == 5

    def test_release_state_has_ten_members(self) -> None:
        assert len(list(ReleaseState)) == 10

    def test_task_impact_has_nine_members(self) -> None:
        assert len(list(TaskImpact)) == 9

    def test_cloud_provider_has_seven_members(self) -> None:
        assert len(list(CloudProvider)) == 7

    def test_resource_type_has_seven_members(self) -> None:
        assert len(list(ResourceType)) == 7

    def test_task_kind_has_thirteen_members(self) -> None:
        assert len(list(TaskKind)) == 13

    def test_entity_kind_has_six_members(self) -> None:
        assert len(list(EntityKind)) == 6

    def test_deploy_urgency_has_three_members(self) -> None:
        assert len(list(DeployUrgency)) == 3


# ── test class 6: auto()-based enum integrity ───────────────────────────


class TestAutoEnumValues:
    def test_session_validation_values_are_contiguous_ints(self) -> None:
        vals = sorted(m.value for m in SessionValidation)
        assert vals == [1, 2, 3, 4, 5]

    def test_session_validation_auto_no_gaps(self) -> None:
        vals = sorted(m.value for m in SessionValidation)
        assert vals == list(range(1, len(vals) + 1))


# ── test class 7: StrEnum value-vs-name audit ───────────────────────────


class TestStrEnumIdentity:
    def test_approval_decision_value_is_name_lowercase(self) -> None:
        for m in ApprovalDecision:
            assert m.value == m.name.lower()

    def test_algorithm_type_value_is_name_lowercase(self) -> None:
        for m in AlgorithmType:
            assert m.value == m.name.lower()

    def test_lifecycle_phase_value_is_name_lowercase(self) -> None:
        for m in LifecyclePhase:
            assert m.value == m.name.lower()

    def test_deploy_urgency_value_is_name_lowercase(self) -> None:
        for m in DeployUrgency:
            assert m.value == m.name.lower()

    def test_disk_pressure_status_value_is_name_lowercase(self) -> None:
        for m in DiskPressureStatus:
            assert m.value == m.name.lower()

    def test_agent_type_value_is_name_lowercase(self) -> None:
        for m in AgentType:
            assert m.value == m.name.lower()

    def test_helper_authority_rank_order(self) -> None:
        order = {
            HelperAuthority.REPOSITORY: 0,
            HelperAuthority.CI_USED: 1,
            HelperAuthority.ECOSYSTEM: 2,
            HelperAuthority.GENERATED: 3,
        }
        for m, expected in order.items():
            assert m.rank() == expected


# ── test class 8: __members__ structural audit ──────────────────────────


class TestEnumMembersStructural:
    def test_all_approval_decision_members_have_string_values(self) -> None:
        for m in ApprovalDecision:
            assert isinstance(m.value, str)

    def test_all_str_enums_have_lowercase_values(self) -> None:
        str_enum_classes = [
            CloudProvider,
            ResourceType,
            DeploymentStrategy,
            ReleaseState,
            Severity,
            Category,
            TaskKind,
            EntityKind,
            ConflictKind,
            ResolutionStrategy,
            TaskImpact,
            DispatchAction,
            CompletionAction,
            DataClassification,
            FractionBasis,
            IsotopeStatus,
            ResultStatus,
            RiskTier,
            StereoStatus,
            StructureRepresentation,
            ValidationStatus,
            HelperAuthority,
            ReleaseVerdictState,
        ]
        for cls in str_enum_classes:
            for m in cls:
                assert m.value == m.value.lower(), f"{cls.__name__}.{m.name} value '{m.value}' is not lowercase"

    def test_session_validation_members_have_int_values(self) -> None:
        for m in SessionValidation:
            assert isinstance(m.value, int)

    def test_dispatch_action_members_are_valid(self) -> None:
        assert len(list(DispatchAction)) >= 1
        for m in DispatchAction:
            assert isinstance(m.value, str)

    def test_completion_action_members_are_valid(self) -> None:
        assert len(list(CompletionAction)) >= 1
        for m in CompletionAction:
            assert isinstance(m.value, str)

    def test_release_verdict_state_members_are_valid(self) -> None:
        assert len(list(ReleaseVerdictState)) >= 2
        for m in ReleaseVerdictState:
            assert isinstance(m.value, str)

    def test_chemistry_data_classification_values(self) -> None:
        assert DataClassification.public.value == "public"
        assert DataClassification.internal.value == "internal"
        assert DataClassification.confidential.value == "confidential"
        assert DataClassification.restricted.value == "restricted"


# ── test class 9: cross-enum consistency ────────────────────────────────


class TestCrossEnumConsistency:
    def test_no_reused_values_across_related_enums(self) -> None:
        values_algostatus = set(m.value for m in AlgorithmStatus)
        values_algotype = set(m.value for m in AlgorithmType)
        assert not (values_algostatus & values_algotype), "AlgorithmStatus and AlgorithmType share values"

    def test_deployment_state_vs_strategy_shared_ok(self) -> None:
        values_state = set(m.value for m in ReleaseState)
        values_strategy = set(m.value for m in DeploymentStrategy)
        overlap = values_state & values_strategy
        assert overlap == {"canary"}, f"Unexpected overlap: {overlap}"

    def test_conflict_kind_vs_resolution_strategy_no_overlap(self) -> None:
        values_kind = set(m.value for m in ConflictKind)
        values_strat = set(m.value for m in ResolutionStrategy)
        assert not (values_kind & values_strat), "ConflictKind and ResolutionStrategy share values"

    def test_task_impact_vs_dispatch_action_no_overlap(self) -> None:
        values_impact = set(m.value for m in TaskImpact)
        values_dispatch = set(m.value for m in DispatchAction)
        assert not (values_impact & values_dispatch), "TaskImpact and DispatchAction share values"

    def test_cloud_provider_vs_resource_type_no_overlap(self) -> None:
        values_provider = set(m.value for m in CloudProvider)
        values_resource = set(m.value for m in ResourceType)
        assert not (values_provider & values_resource), "CloudProvider and ResourceType share values"
