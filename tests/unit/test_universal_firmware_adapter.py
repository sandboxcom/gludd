"""Universal-executor acceptance tests for Arduino firmware tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.ai_ml.accelerators import (
    AcceleratorKind,
    AcceleratorPlanner,
    HardwareDescriptor,
)
from general_ludd.ai_ml.policy import PolicyEngine
from general_ludd.chemistry.polymer_design import PolymerDesignAdapter
from general_ludd.embedded.firmware import (
    ArduinoFirmwareAdapter,
    ArduinoToolRunner,
    BoardProfile,
    CommandEvidence,
    FirmwareToolchain,
)
from general_ludd.execution.universal_task import (
    ExecutionTarget,
    ModelProfileOrigin,
    PinnedProfileOriginVerifier,
    TaskStatus,
    UniversalTaskExecutor,
    UniversalTaskRequest,
    UniversalTaskResult,
)
from general_ludd.execution.universal_task_runtime import UniversalTaskRuntime
from general_ludd.scheduling.scheduler import Scheduler

CAPABILITY = "arduino-cpp"
TOOL = "arduino_toolchain"
SOURCE = """\
void setup() {
  Serial.begin(115200);
}

void loop() {
  Serial.println("GLUDD_OK");
  delay(1000);
}
"""


class _Gateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append(
            {"profile_id": profile_id, "messages": messages, "kwargs": kwargs}
        )
        return SimpleNamespace(content=self.content, cost_estimate=0.02)


class _PipelineRunner:
    def __init__(self, evidence: dict[str, object] | None = None) -> None:
        self.evidence = evidence or _pipeline_evidence()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        return self.evidence


class _NonBooleanOriginVerifier:
    def verify(self, origin: ModelProfileOrigin) -> bool:
        del origin
        return cast(bool, 1)


def _stage(
    name: str,
    *,
    exit_code: int = 0,
    stdout: str = "",
    argv: tuple[str, ...] | None = None,
) -> dict[str, object]:
    return {
        "stage": name,
        "argv": list(argv or (name,)),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": "" if exit_code == 0 else "verification failed",
        "tool_version": f"{name}-1.0",
        "artifact_sha256": hashlib.sha256(b"firmware-elf").hexdigest()
        if name == "compile" and exit_code == 0
        else None,
        "bounded_termination": name == "simulate",
    }


def _pipeline_evidence() -> dict[str, object]:
    return {
        "compile": _stage("compile", argv=("arduino-cli", "compile")),
        "static_check": _stage("static_check", argv=("cppcheck",)),
        "simulate": _stage(
            "simulate",
            stdout="booted GLUDD_OK",
            argv=("simavr", "firmware.elf"),
        ),
    }


def _candidate(*, source: str = SOURCE, board: str = "arduino:avr:uno") -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "language": "arduino-cpp",
            "board_fqbn": board,
            "source": source,
            "assumptions": ["Serial is available"],
        }
    )


def _polymer_candidate() -> str:
    return json.dumps(
        {
            "candidate_id": "poly-glucose-001",
            "monomers": ["glucose"],
            "repeat_unit": "glucose-repeat-unit",
            "properties": [
                {
                    "name": "glass_transition_temperature",
                    "value": 118.0,
                    "unit": "degC",
                    "uncertainty": 4.0,
                    "method_id": "bounded-qspr-v1",
                }
            ],
            "synthesis_scale": "lab",
            "facility_controls": [],
            "validation": {
                "checks": ["unit_consistency", "convergence"],
                "converged": True,
                "iterations": 12,
            },
            "provenance": {
                "source": {
                    "locator": "doi:10.0000/polymer-example",
                    "citation": "Bounded polymer example",
                    "accessed_at": "2026-09-16",
                },
                "method": "bounded-qspr-v1",
                "conditions": {"temperature": {"value": 25.0, "unit": "degC"}},
                "code": {
                    "repository": "gludd",
                    "commit": "0123456789abcdef",
                    "module": "general_ludd.chemistry.polymer_design",
                },
                "raw_artifact": {
                    "uri": "artifact://polymer/example-001",
                    "digest": "a" * 64,
                },
            },
        }
    )


def _planner() -> AcceleratorPlanner:
    local = HardwareDescriptor(
        kind=AcceleratorKind.GPU,
        name="Local GPU",
        sku="local-gpu",
        region="local",
        provider="local",
        approved=True,
    )
    azure = HardwareDescriptor(
        kind=AcceleratorKind.CLOUD,
        name="Azure GPU",
        sku="Standard_NC24ads_A100_v4",
        region="eastus",
        provider="azure",
        approved=True,
    )
    catalog_provider = HardwareDescriptor(
        kind=AcceleratorKind.CLOUD,
        name="FreeLLMAPI-admitted Groq service",
        sku="freellmapi-groq-managed",
        region="provider-managed",
        provider="groq",
        approved=True,
    )
    return AcceleratorPlanner(
        approved_cloud_skus=frozenset({azure.sku, catalog_provider.sku}),
        local_hardware=(local,),
        cloud_catalog=(azure, catalog_provider),
    )


def _target(
    provider: str,
    cost: float,
    *,
    profile_id: str | None = None,
    capabilities: frozenset[str] = frozenset({CAPABILITY}),
    origin_source: str = "operator-configured",
    origin_evidence_sha256: str | None = None,
) -> ExecutionTarget:
    local = provider == "local"
    accelerator_sku = {
        "local": "local-gpu",
        "azure": "Standard_NC24ads_A100_v4",
        "groq": "freellmapi-groq-managed",
    }[provider]
    resolved_profile_id = profile_id or f"{provider}-firmware"
    protocol = (
        "gludd-freellmapi-probe-profile-v1"
        if origin_source == "freellmapi"
        else "gludd-native-profile-v1"
    )
    return ExecutionTarget.bind_origin(
        profile_id=resolved_profile_id,
        provider=provider,
        accelerator_sku=accelerator_sku,
        capabilities=capabilities,
        allowed_data_classifications=frozenset(
            {"public", "internal", "confidential", "restricted"}
            if local
            else {"public", "internal"}
        ),
        estimated_cost_usd=cost,
        healthy=True,
        health_evidence=f"{provider} health probe",
        capability_evidence="Arduino C++ compile benchmark",
        cost_evidence=f"{provider} measured cost",
        privacy_evidence="offline isolation" if local else "approved provider egress",
        offline=local,
        origin_source=origin_source,
        origin_protocol=protocol,
        origin_evidence_sha256=origin_evidence_sha256
        or hashlib.sha256(
            f"{origin_source}\0{provider}\0{resolved_profile_id}".encode()
        ).hexdigest(),
    )


def _request(
    *,
    classification: str = "public",
    allowed_tools: frozenset[str] = frozenset({TOOL}),
    metadata: dict[str, object] | None = None,
) -> UniversalTaskRequest:
    return UniversalTaskRequest(
        task_id="firmware-task-1",
        capability=CAPABILITY,
        instruction="Blink the built-in LED and print GLUDD_OK once per second.",
        budget_usd=1.0,
        data_classification=classification,
        allowed_tools=allowed_tools,
        resources=frozenset({"firmware-validation"}),
        metadata=metadata
        or {
            "board_fqbn": "arduino:avr:uno",
            "expected_serial": "GLUDD_OK",
            "physical_device_access": False,
        },
    )


def _polymer_request() -> UniversalTaskRequest:
    return UniversalTaskRequest(
        task_id="polymer-task-1",
        capability="polymer_design",
        instruction="Design a bounded glucose-derived polymer candidate.",
        budget_usd=1.0,
        data_classification="public",
        resources=frozenset({"polymer-design"}),
        metadata={"requested_properties": ["glass_transition_temperature"]},
    )


def _verifier(*targets: ExecutionTarget) -> PinnedProfileOriginVerifier:
    origins = tuple(target.profile_origin for target in targets)
    assert all(origin is not None for origin in origins)
    return PinnedProfileOriginVerifier.from_origins(
        tuple(origin for origin in origins if origin is not None)
    )


def _execute(
    request: UniversalTaskRequest,
    gateway: _Gateway,
    runner: _PipelineRunner | None,
    *,
    targets: tuple[ExecutionTarget, ...] | None = None,
) -> UniversalTaskResult:
    resolved_targets = targets or (_target("local", 0.2), _target("azure", 0.1))
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: resolved_targets,
        tool_runner=runner,
        profile_origin_verifier=_verifier(*resolved_targets),
    )
    return executor.execute(request, ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))


def test_one_runtime_routes_chemistry_and_firmware_via_freellmapi_origin() -> None:
    gateway = _Gateway(_polymer_candidate())
    runner = _PipelineRunner()
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        capabilities=frozenset({CAPABILITY, "polymer_design"}),
        origin_source="freellmapi",
        origin_evidence_sha256="f" * 64,
    )
    origin = target.profile_origin
    assert origin is not None
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=runner,
        profile_origin_verifier=_verifier(target),
    )
    runtime = UniversalTaskRuntime(
        executor=executor,
        adapters=(
            PolymerDesignAdapter(policy_engine=PolicyEngine()),
            ArduinoFirmwareAdapter(policy_engine=PolicyEngine()),
        ),
    )
    polymer = runtime.execute(_polymer_request())
    gateway.content = _candidate()
    firmware = runtime.execute(_request(classification="public"))

    assert polymer.status is TaskStatus.SUCCEEDED
    assert firmware.status is TaskStatus.SUCCEEDED
    assert polymer.route is not None and polymer.route.selected_provider == "groq"
    assert firmware.route is not None and firmware.route.selected_provider == "groq"
    assert polymer.route.selected_profile_origin == origin
    assert firmware.route.selected_profile_origin == origin
    assert type(polymer.candidate).__module__ == "general_ludd.chemistry.polymer_design"
    assert type(firmware.candidate).__module__ == "general_ludd.embedded.firmware"
    assert len(gateway.calls) == 2


def test_self_improvement_only_target_cannot_absorb_polymer_or_firmware() -> None:
    """Exact capability routing refuses instead of falling through to self-improve."""
    gateway = _Gateway(_polymer_candidate())
    self_improve_only = _target(
        "local",
        0.0,
        capabilities=frozenset({"self_improve.proposal"}),
    )
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (self_improve_only,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=_verifier(self_improve_only),
    )
    runtime = UniversalTaskRuntime(
        executor=executor,
        adapters=(
            PolymerDesignAdapter(policy_engine=PolicyEngine()),
            ArduinoFirmwareAdapter(policy_engine=PolicyEngine()),
        ),
    )

    polymer = runtime.execute(_polymer_request())
    firmware = runtime.execute(_request())

    assert polymer.status is TaskStatus.REFUSED
    assert firmware.status is TaskStatus.REFUSED
    assert polymer.reasons == ("no_eligible_target",)
    assert firmware.reasons == ("no_eligible_target",)
    assert gateway.calls == []
    for result in (polymer, firmware):
        assert result.route is not None
        assert result.route.evaluations[0].reasons == ("capability_not_supported",)


def test_duplicate_profile_identity_cannot_swap_domain_or_provider() -> None:
    """One ID cannot route through Groq then execute as local self-improvement."""
    shared_profile_id = "freellmapi-shared-0123456789abcdef0123"
    polymer_target = _target(
        "groq",
        0.0,
        profile_id=shared_profile_id,
        capabilities=frozenset({"polymer_design"}),
        origin_source="freellmapi",
    )
    self_improve_target = _target(
        "local",
        0.1,
        profile_id=shared_profile_id,
        capabilities=frozenset({"self_improve.proposal"}),
    )
    gateway = _Gateway(_polymer_candidate())
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (polymer_target, self_improve_target),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=_verifier(
            polymer_target,
            self_improve_target,
        ),
    )

    result = executor.execute(
        _polymer_request(),
        PolymerDesignAdapter(policy_engine=PolicyEngine()),
    )

    assert result.status is TaskStatus.REFUSED
    assert result.reasons == ("no_eligible_target",)
    assert result.route is not None
    assert all(
        "profile_id_ambiguous" in evaluation.reasons
        for evaluation in result.route.evaluations
    )
    assert gateway.calls == []


def test_discovered_profile_requires_the_exact_pinned_origin_receipt() -> None:
    """Self-consistent but untrusted origin claims never reach the gateway."""
    trusted_target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
        origin_evidence_sha256="f" * 64,
    )
    forged_target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
        origin_evidence_sha256="e" * 64,
    )
    gateway = _Gateway(_candidate())

    missing_verifier = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (forged_target,),
        tool_runner=_PipelineRunner(),
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))
    wrong_receipt = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (forged_target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=_verifier(trusted_target),
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))

    assert missing_verifier.status is TaskStatus.REFUSED
    assert wrong_receipt.status is TaskStatus.REFUSED
    assert missing_verifier.route is not None
    assert wrong_receipt.route is not None
    assert "profile_origin_verifier_unavailable" in (
        missing_verifier.route.evaluations[0].reasons
    )
    assert "profile_origin_untrusted" in wrong_receipt.route.evaluations[0].reasons
    assert gateway.calls == []


def test_ambiguous_origin_verifier_verdict_fails_closed() -> None:
    """Truthy verifier objects cannot impersonate an exact trust decision."""
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )
    gateway = _Gateway(_candidate())
    result = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=_NonBooleanOriginVerifier(),
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    assert result.route.evaluations[0].reasons == (
        "profile_origin_verification_invalid",
    )
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("malformed_origin", "reason"),
    [
        (None, "profile_origin_required"),
        (object(), "profile_origin_invalid"),
    ],
)
def test_corrupted_target_cannot_erase_or_replace_its_origin_receipt(
    malformed_origin: object,
    reason: str,
) -> None:
    """The route boundary revalidates receipts even after target construction."""
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )
    verifier = _verifier(target)
    object.__setattr__(target, "profile_origin", malformed_origin)
    gateway = _Gateway(_candidate())

    result = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=verifier,
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    assert result.route.evaluations[0].reasons == (reason,)
    assert gateway.calls == []


def test_mutated_origin_fields_cannot_reuse_a_pinned_receipt() -> None:
    """Pinned receipt identity is rehashed at the route boundary."""
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )
    verifier = _verifier(target)
    origin = target.profile_origin
    assert origin is not None
    object.__setattr__(origin, "evidence_sha256", "e" * 64)
    gateway = _Gateway(_candidate())

    assert verifier.verify(origin) is False
    result = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=verifier,
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    assert result.route.evaluations[0].reasons == ("profile_origin_invalid",)
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("provider", "accelerator_sku", "offline"),
    [
        ("local", "local-gpu", True),
        ("azure", "Standard_NC24ads_A100_v4", False),
    ],
)
def test_mutated_target_cannot_replay_receipt_across_native_providers(
    provider: str,
    accelerator_sku: str,
    offline: bool,
) -> None:
    """Route-time binding blocks post-construction local and Azure replay."""
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )
    verifier = _verifier(target)
    object.__setattr__(target, "provider", provider)
    object.__setattr__(target, "accelerator_sku", accelerator_sku)
    object.__setattr__(target, "offline", offline)
    gateway = _Gateway(_candidate())

    result = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=verifier,
    ).execute(_request(), ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    assert result.route.evaluations[0].reasons == (
        "profile_origin_target_mismatch",
    )
    assert gateway.calls == []


def test_mutated_target_cannot_expand_a_pinned_capability_scope() -> None:
    """A pinned Arduino receipt cannot be widened into a polymer route."""
    target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )
    verifier = _verifier(target)
    object.__setattr__(
        target,
        "capabilities",
        frozenset({CAPABILITY, "polymer_design"}),
    )
    gateway = _Gateway(_polymer_candidate())

    result = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=_PipelineRunner(),
        profile_origin_verifier=verifier,
    ).execute(_polymer_request(), PolymerDesignAdapter(policy_engine=PolicyEngine()))

    assert result.status is TaskStatus.REFUSED
    assert result.route is not None
    assert result.route.evaluations[0].reasons == (
        "profile_origin_target_mismatch",
    )
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("provider", "accelerator_sku", "offline"),
    [
        ("local", "local-gpu", True),
        ("azure", "Standard_NC24ads_A100_v4", False),
    ],
)
def test_freellmapi_origin_receipt_cannot_cross_native_provider_boundaries(
    provider: str,
    accelerator_sku: str,
    offline: bool,
) -> None:
    catalog_target = _target(
        "groq",
        0.0,
        profile_id="freellmapi-groq-0123456789abcdef0123",
        origin_source="freellmapi",
    )

    with pytest.raises(ValueError, match="profile_origin_target_mismatch"):
        replace(
            catalog_target,
            provider=provider,
            accelerator_sku=accelerator_sku,
            offline=offline,
        )

    with pytest.raises(ValueError, match="profile_origin_target_mismatch"):
        replace(
            catalog_target,
            capabilities=frozenset({CAPABILITY, "polymer_design"}),
        )


@pytest.mark.parametrize(
    ("classification", "local_cost", "azure_cost", "expected_provider"),
    [
        ("restricted", 0.2, 0.1, "local"),
        ("public", 0.2, 0.1, "azure"),
        ("public", 0.05, 0.1, "local"),
    ],
)
def test_same_universal_executor_routes_and_verifies_local_or_azure_firmware(
    classification: str,
    local_cost: float,
    azure_cost: float,
    expected_provider: str,
) -> None:
    gateway = _Gateway(_candidate())
    runner = _PipelineRunner()

    result = _execute(
        _request(classification=classification),
        gateway,
        runner,
        targets=(_target("local", local_cost), _target("azure", azure_cost)),
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert result.route is not None
    assert result.route.selected_provider == expected_provider
    assert gateway.calls[0]["profile_id"] == f"{expected_provider}-firmware"
    assert runner.calls[0][0] == TOOL
    assert runner.calls[0][1]["board_fqbn"] == "arduino:avr:uno"
    policy = result.evidence["policy"]
    validation = result.evidence["validation"]
    provenance = result.evidence["provenance"]
    safety = result.evidence["safety"]
    assert isinstance(policy, dict) and policy["allowed"] is True
    assert isinstance(validation, dict) and validation["status"] == "validated"
    assert isinstance(provenance, dict) and provenance["compile_artifact_sha256"]
    assert isinstance(safety, dict) and safety["physical_device_access"] is False


@pytest.mark.parametrize(
    ("task_request", "reason"),
    [
        (
            _request(metadata={"expected_serial": "GLUDD_OK"}),
            "board_fqbn_required",
        ),
        (
            _request(
                metadata={
                    "board_fqbn": "arduino:avr:uno",
                    "expected_serial": "GLUDD_OK",
                    "physical_device_access": True,
                }
            ),
            "physical_device_access_requires_separate_authority",
        ),
        (_request(allowed_tools=frozenset()), "arduino_toolchain_not_allowed"),
    ],
)
def test_firmware_preflight_refuses_invalid_or_unauthorized_requests(
    task_request: UniversalTaskRequest,
    reason: str,
) -> None:
    gateway = _Gateway(_candidate())

    result = _execute(task_request, gateway, _PipelineRunner())

    assert result.status is TaskStatus.REFUSED
    assert reason in result.reasons
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("content", "evidence", "reason"),
    [
        (_candidate(board="arduino:avr:mega"), _pipeline_evidence(), "candidate_board_mismatch"),
        (
            _candidate(source='void setup() {}\nvoid loop() { system("bad"); }'),
            _pipeline_evidence(),
            "structured_candidate_required",
        ),
        (_candidate(), {"compile": _stage("compile")}, "tool_evidence_incomplete"),
        (
            _candidate(),
            {
                **_pipeline_evidence(),
                "compile": _stage(
                    "compile",
                    argv=("arduino-cli", "upload"),
                ),
            },
            "compile_evidence_contains_an_unsafe_or_missing_command",
        ),
        (
            _candidate(),
            {
                **_pipeline_evidence(),
                "simulate": _stage("simulate", stdout="wrong output"),
            },
            "simulator_observable_missing",
        ),
    ],
)
def test_uncompiled_unsafe_or_unverifiable_candidates_never_succeed(
    content: str,
    evidence: dict[str, object],
    reason: str,
) -> None:
    result = _execute(_request(), _Gateway(content), _PipelineRunner(evidence))

    assert result.status is TaskStatus.REFUSED
    assert reason in result.reasons


class _Toolchain(FirmwareToolchain):
    def __init__(self, *, compile_ok: bool = True) -> None:
        self.compile_ok = compile_ok
        self.calls: list[str] = []

    def compile(self, source: str, board: BoardProfile) -> CommandEvidence:
        self.calls.append("compile")
        return CommandEvidence(
            stage="compile",
            argv=("arduino-cli", "compile", "--fqbn", board.fqbn),
            exit_code=0 if self.compile_ok else 1,
            stdout="",
            stderr="" if self.compile_ok else "failed",
            tool_version="arduino-cli-1",
            artifact_sha256=hashlib.sha256(source.encode()).hexdigest()
            if self.compile_ok
            else None,
        )

    def static_check(self, source: str, board: BoardProfile) -> CommandEvidence:
        del source, board
        self.calls.append("static_check")
        return CommandEvidence(
            stage="static_check",
            argv=("cppcheck",),
            exit_code=0,
            stdout="",
            stderr="",
            tool_version="cppcheck-1",
        )

    def simulate(
        self,
        source: str,
        board: BoardProfile,
        compile_evidence: CommandEvidence,
    ) -> CommandEvidence:
        del source, board, compile_evidence
        self.calls.append("simulate")
        return CommandEvidence(
            stage="simulate",
            argv=("simavr",),
            exit_code=0,
            stdout="GLUDD_OK",
            stderr="",
            tool_version="simavr-1",
        )


def test_tool_runner_bridges_real_toolchain_without_bypassing_failed_compile() -> None:
    toolchain = _Toolchain()
    runner = ArduinoToolRunner(toolchain)
    payload: dict[str, object] = {
        "source": SOURCE,
        "board_fqbn": "arduino:avr:uno",
    }

    evidence = runner.run(TOOL, payload)

    assert toolchain.calls == ["compile", "static_check", "simulate"]
    assert set(evidence) == {"compile", "static_check", "simulate"}

    failed = _Toolchain(compile_ok=False)
    partial = ArduinoToolRunner(failed).run(TOOL, payload)
    assert failed.calls == ["compile"]
    assert set(partial) == {"compile"}


@pytest.mark.parametrize(
    "tool_name,payload",
    [
        ("shell", {"source": SOURCE, "board_fqbn": "arduino:avr:uno"}),
        (TOOL, {"board_fqbn": "arduino:avr:uno"}),
        (TOOL, {"source": SOURCE, "board_fqbn": "unknown"}),
    ],
)
def test_tool_runner_rejects_unbounded_tools_and_invalid_payloads(
    tool_name: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ArduinoToolRunner(_Toolchain()).run(tool_name, payload)


def test_missing_or_crashing_tool_runner_fails_closed() -> None:
    missing = _execute(_request(), _Gateway(_candidate()), None)
    assert missing.status is TaskStatus.REFUSED
    assert missing.reasons == ("arduino_toolchain_unavailable",)

    class _BrokenRunner(_PipelineRunner):
        def run(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("tool details must not escape")

    crashed = _execute(_request(), _Gateway(_candidate()), _BrokenRunner())
    assert crashed.status is TaskStatus.REFUSED
    assert crashed.reasons == ("arduino_toolchain_failed:RuntimeError",)


def test_adapter_rejects_wrong_candidate_type_and_invalid_deadline() -> None:
    adapter = ArduinoFirmwareAdapter(policy_engine=PolicyEngine())
    assessment = adapter.assess_candidate(_request(), {"source": SOURCE}, None)
    assert assessment.accepted is False
    assert assessment.reasons == ("structured_candidate_required",)

    gateway = _Gateway(_candidate())
    request = replace(
        _request(),
        metadata={
            "board_fqbn": "arduino:avr:uno",
            "expected_serial": "GLUDD_OK",
            "deadline_s": 0,
        },
    )
    result = _execute(request, gateway, _PipelineRunner())
    assert result.status is TaskStatus.REFUSED
    assert result.reasons == ("policy_refused:deadline_s must be a positive integer",)
    assert gateway.calls == []
