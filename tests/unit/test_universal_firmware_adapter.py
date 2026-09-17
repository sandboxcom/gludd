"""Universal-executor acceptance tests for Arduino firmware tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

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
    TaskStatus,
    UniversalTaskExecutor,
    UniversalTaskRequest,
)
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
    return AcceleratorPlanner(
        approved_cloud_skus=frozenset({azure.sku}),
        local_hardware=(local,),
        cloud_catalog=(azure,),
    )


def _target(provider: str, cost: float) -> ExecutionTarget:
    local = provider == "local"
    return ExecutionTarget(
        profile_id=f"{provider}-firmware",
        provider=provider,
        accelerator_sku="local-gpu" if local else "Standard_NC24ads_A100_v4",
        capabilities=frozenset({CAPABILITY}),
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
        privacy_evidence="offline isolation" if local else "approved Azure egress",
        offline=local,
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


def _execute(
    request: UniversalTaskRequest,
    gateway: _Gateway,
    runner: _PipelineRunner | None,
    *,
    targets: tuple[ExecutionTarget, ...] | None = None,
) -> object:
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: targets or (_target("local", 0.2), _target("azure", 0.1)),
        tool_runner=runner,
    )
    return executor.execute(request, ArduinoFirmwareAdapter(policy_engine=PolicyEngine()))


def test_one_executor_instance_runs_independent_chemistry_and_firmware_adapters() -> None:
    gateway = _Gateway(_polymer_candidate())
    runner = _PipelineRunner()
    target = replace(
        _target("local", 0.05),
        capabilities=frozenset({CAPABILITY, "polymer_design"}),
    )
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=Scheduler(),
        accelerator_planner=_planner(),
        target_source=lambda: (target,),
        tool_runner=runner,
    )
    polymer_request = UniversalTaskRequest(
        task_id="polymer-task-1",
        capability="polymer_design",
        instruction="Design a bounded glucose-derived polymer candidate.",
        budget_usd=1.0,
        data_classification="restricted",
        resources=frozenset({"polymer-design"}),
        metadata={"requested_properties": ["glass_transition_temperature"]},
    )

    polymer = executor.execute(
        polymer_request,
        PolymerDesignAdapter(policy_engine=PolicyEngine()),
    )
    gateway.content = _candidate()
    firmware = executor.execute(
        _request(classification="restricted"),
        ArduinoFirmwareAdapter(policy_engine=PolicyEngine()),
    )

    assert polymer.status is TaskStatus.SUCCEEDED
    assert firmware.status is TaskStatus.SUCCEEDED
    assert polymer.route is not None and polymer.route.selected_provider == "local"
    assert firmware.route is not None and firmware.route.selected_provider == "local"
    assert type(polymer.candidate).__module__ == "general_ludd.chemistry.polymer_design"
    assert type(firmware.candidate).__module__ == "general_ludd.embedded.firmware"
    assert len(gateway.calls) == 2


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
    assert result.evidence["policy"]["allowed"] is True
    assert result.evidence["validation"]["status"] == "validated"
    assert result.evidence["provenance"]["compile_artifact_sha256"]
    assert result.evidence["safety"]["physical_device_access"] is False


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
    payload = {"source": SOURCE, "board_fqbn": "arduino:avr:uno"}

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
