"""Contract tests for a board-qualified, evidence-gated firmware workload."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.embedded.firmware import (
    ArduinoFirmwareWorkload,
    BoardProfile,
    CommandEvidence,
    FirmwareRequest,
    FirmwareStatus,
    FirmwareToolchain,
    ModelRoute,
    SubprocessArduinoToolchain,
    supported_board,
)

VALID_SOURCE = """\
void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  Serial.println("GLUDD_OK");
  delay(1000);
}
"""


def _model_output(*, source: str = VALID_SOURCE, fqbn: str = "arduino:avr:uno") -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "language": "arduino-cpp",
            "board_fqbn": fqbn,
            "source": source,
            "assumptions": ["LED_BUILTIN is available"],
        }
    )


def _request(
    *,
    request_id: str = "fw-001",
    objective: str = "Blink the built-in LED and print GLUDD_OK once per second",
    board_fqbn: str = "arduino:avr:uno",
    expected_serial: str = "GLUDD_OK",
    physical_device_access: bool = False,
    data_classification: str = "internal",
) -> FirmwareRequest:
    return FirmwareRequest(
        request_id=request_id,
        objective=objective,
        board_fqbn=board_fqbn,
        expected_serial=expected_serial,
        physical_device_access=physical_device_access,
        data_classification=data_classification,
    )


def _route(
    *,
    backend: str = "local",
    profile_id: str = "local-code-model",
    endpoint_id: str = "ollama-local",
    decision_id: str = "route-001",
    capability_evidence: tuple[str, ...] = ("arduino-cpp",),
    health_evidence: str = "healthy",
    cost_usd: float = 0.0,
    data_classification: str = "internal",
) -> ModelRoute:
    return ModelRoute(
        backend=backend,
        profile_id=profile_id,
        endpoint_id=endpoint_id,
        decision_id=decision_id,
        capability_evidence=capability_evidence,
        health_evidence=health_evidence,
        cost_usd=cost_usd,
        data_classification=data_classification,
    )


def _evidence(stage: str, *, ok: bool = True, stdout: str = "") -> CommandEvidence:
    digest = hashlib.sha256(b"firmware").hexdigest() if stage == "compile" and ok else None
    return CommandEvidence(
        stage=stage,
        argv=(stage,),
        exit_code=0 if ok else 1,
        stdout=stdout,
        stderr="" if ok else f"{stage} failed",
        tool_version=f"{stage}-1.0",
        artifact_sha256=digest,
    )


class RecordingToolchain(FirmwareToolchain):
    def __init__(
        self,
        *,
        compile_ok: bool = True,
        static_ok: bool = True,
        simulation_ok: bool = True,
        simulation_output: str = "GLUDD_OK",
        compile_digest: bool = True,
    ) -> None:
        self.compile_ok = compile_ok
        self.static_ok = static_ok
        self.simulation_ok = simulation_ok
        self.simulation_output = simulation_output
        self.compile_digest = compile_digest
        self.calls: list[str] = []

    def compile(self, source: str, board: BoardProfile) -> CommandEvidence:
        self.calls.append("compile")
        evidence = _evidence("compile", ok=self.compile_ok)
        if self.compile_ok and not self.compile_digest:
            return CommandEvidence(
                stage=evidence.stage,
                argv=evidence.argv,
                exit_code=evidence.exit_code,
                stdout=evidence.stdout,
                stderr=evidence.stderr,
                tool_version=evidence.tool_version,
            )
        return evidence

    def static_check(self, source: str, board: BoardProfile) -> CommandEvidence:
        self.calls.append("static_check")
        return _evidence("static_check", ok=self.static_ok)

    def simulate(
        self,
        source: str,
        board: BoardProfile,
        compile_evidence: CommandEvidence,
    ) -> CommandEvidence:
        self.calls.append("simulate")
        return _evidence(
            "simulate",
            ok=self.simulation_ok,
            stdout=self.simulation_output,
        )


def test_supported_board_is_fully_qualified_and_unknown_board_fails_closed() -> None:
    board = supported_board("arduino:avr:uno")

    assert board.fqbn == "arduino:avr:uno"
    assert board.mcu == "atmega328p"
    assert board.clock_hz == 16_000_000
    assert board.simulator == "simavr"

    with pytest.raises(ValueError, match="unsupported board FQBN"):
        supported_board("arduino:unknown:maybe")


def test_generation_prompt_requires_strict_json_board_and_no_upload() -> None:
    messages = ArduinoFirmwareWorkload().build_messages(_request())
    rendered = "\n".join(str(message["content"]) for message in messages)

    assert "arduino:avr:uno" in rendered
    assert "strict JSON" in rendered
    assert "GLUDD_OK" in rendered
    assert "must not upload" in rendered.lower()
    assert "compile" in rendered.lower()
    assert "simulate" in rendered.lower()


@pytest.mark.parametrize(
    "raw, reason",
    [
        (VALID_SOURCE, "strict JSON"),
        (f"```cpp\n{VALID_SOURCE}\n```", "strict JSON"),
        (json.dumps({"source": VALID_SOURCE}), "schema"),
        (_model_output(fqbn="arduino:avr:mega"), "board FQBN"),
        (_model_output(source="// TODO implement\nvoid setup() {}\nvoid loop() {}"), "placeholder"),
        (_model_output(source="void setup() {}"), "setup.*loop"),
        (_model_output(source="void setup() {}\nvoid loop() { system(\"rm -rf /\"); }"), "unsafe"),
    ],
)
def test_model_output_must_be_structured_complete_safe_and_board_matched(
    raw: str,
    reason: str,
) -> None:
    result = ArduinoFirmwareWorkload().execute(
        _request(),
        _route(),
        raw,
        RecordingToolchain(),
    )

    assert result.status is FirmwareStatus.REJECTED
    assert any(re.search(reason, item, re.IGNORECASE) for item in result.reasons)
    assert result.completed is False


def test_physical_device_access_is_off_by_default_and_requires_separate_authority() -> None:
    assert _request().physical_device_access is False

    result = ArduinoFirmwareWorkload().execute(
        _request(physical_device_access=True),
        _route(),
        _model_output(),
        RecordingToolchain(),
    )

    assert result.status is FirmwareStatus.REFUSED
    assert result.completed is False
    assert "physical" in " ".join(result.reasons).lower()


@pytest.mark.parametrize("backend", ["local", "azure"])
def test_complete_local_and_azure_runs_require_all_verification_evidence(backend: str) -> None:
    toolchain = RecordingToolchain()
    result = ArduinoFirmwareWorkload().execute(
        _request(),
        _route(
            backend=backend,
            profile_id=f"{backend}-code-model",
            endpoint_id=f"{backend}-endpoint",
            cost_usd=0.02 if backend == "azure" else 0.0,
        ),
        _model_output(),
        toolchain,
    )

    assert result.status is FirmwareStatus.SUCCEEDED
    assert result.completed is True
    assert toolchain.calls == ["compile", "static_check", "simulate"]
    assert [item.stage for item in result.evidence] == [
        "compile",
        "static_check",
        "simulate",
    ]
    assert result.provenance["route"]["backend"] == backend
    assert result.provenance["route"]["decision_id"] == "route-001"
    assert result.provenance["source_sha256"]
    assert result.provenance["compile_artifact_sha256"]


@pytest.mark.parametrize(
    "toolchain, expected_calls, reason",
    [
        (RecordingToolchain(compile_ok=False), ["compile"], "compile"),
        (RecordingToolchain(compile_digest=False), ["compile"], "digest"),
        (
            RecordingToolchain(static_ok=False),
            ["compile", "static_check"],
            "static",
        ),
        (
            RecordingToolchain(simulation_ok=False),
            ["compile", "static_check", "simulate"],
            "simulat",
        ),
        (
            RecordingToolchain(simulation_output="booted but no sentinel"),
            ["compile", "static_check", "simulate"],
            "expected serial",
        ),
    ],
)
def test_failed_or_unverifiable_stages_never_report_completion(
    toolchain: RecordingToolchain,
    expected_calls: list[str],
    reason: str,
) -> None:
    result = ArduinoFirmwareWorkload().execute(
        _request(),
        _route(),
        _model_output(),
        toolchain,
    )

    assert result.status is FirmwareStatus.REJECTED
    assert result.completed is False
    assert toolchain.calls == expected_calls
    assert reason in " ".join(result.reasons).lower()


def test_route_evidence_rejects_unhealthy_incapable_or_privacy_unsafe_backend() -> None:
    workload = ArduinoFirmwareWorkload()
    cases = [
        _route(health_evidence="unhealthy"),
        _route(capability_evidence=()),
        _route(backend="azure", data_classification="restricted"),
        _route(backend="other"),
    ]

    for route in cases:
        result = workload.execute(_request(), route, _model_output(), RecordingToolchain())
        assert result.status is FirmwareStatus.REFUSED
        assert result.completed is False


@pytest.mark.parametrize(
    "firmware_request, route, expected",
    [
        (_request(), _route(cost_usd=-1.0), "cost"),
        (_request(), _route(data_classification="public"), "classification"),
        (
            _request(data_classification="restricted"),
            _route(backend="azure", data_classification="restricted"),
            "local",
        ),
        (_request(), _route(decision_id=""), "provenance"),
    ],
)
def test_additional_route_evidence_is_fail_closed(
    firmware_request: FirmwareRequest,
    route: ModelRoute,
    expected: str,
) -> None:
    result = ArduinoFirmwareWorkload().execute(
        firmware_request,
        route,
        _model_output(),
        RecordingToolchain(),
    )

    assert result.status is FirmwareStatus.REFUSED
    assert expected in " ".join(result.reasons).lower()


def test_unknown_requested_board_is_unsupported_before_model_output() -> None:
    result = ArduinoFirmwareWorkload().execute(
        _request(board_fqbn="vendor:arch:unknown"),
        _route(),
        "{}",
        RecordingToolchain(),
    )

    assert result.status is FirmwareStatus.UNSUPPORTED
    assert "unsupported board" in " ".join(result.reasons)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ([], "object"),
        (
            {
                "schema_version": "1.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE,
                "assumptions": [],
                "surprise": True,
            },
            "unknown fields",
        ),
        (
            {
                "schema_version": "2.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE,
                "assumptions": [],
            },
            "schema version",
        ),
        (
            {
                "schema_version": "1.0",
                "language": "python",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE,
                "assumptions": [],
            },
            "language",
        ),
        (
            {
                "schema_version": "1.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": "",
                "assumptions": [],
            },
            "non-empty",
        ),
        (
            {
                "schema_version": "1.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE,
                "assumptions": "none",
            },
            "string array",
        ),
        (
            {
                "schema_version": "1.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE + (" " * 256_000),
                "assumptions": [],
            },
            "size limit",
        ),
        (
            {
                "schema_version": "1.0",
                "language": "arduino-cpp",
                "board_fqbn": "arduino:avr:uno",
                "source": VALID_SOURCE.replace("void setup()", "void setup() /* ``` */"),
                "assumptions": [],
            },
            "Markdown fences",
        ),
    ],
)
def test_candidate_schema_edge_cases_are_rejected(payload: object, expected: str) -> None:
    result = ArduinoFirmwareWorkload().execute(
        _request(),
        _route(),
        json.dumps(payload),
        RecordingToolchain(),
    )

    assert result.status is FirmwareStatus.REJECTED
    assert expected.lower() in " ".join(result.reasons).lower()


class MissingStageToolchain(RecordingToolchain):
    def __init__(self, missing_stage: str) -> None:
        super().__init__()
        self.missing_stage = missing_stage

    def compile(self, source: str, board: BoardProfile) -> CommandEvidence:
        if self.missing_stage == "compile":
            raise FileNotFoundError("arduino-cli")
        return super().compile(source, board)

    def static_check(self, source: str, board: BoardProfile) -> CommandEvidence:
        if self.missing_stage == "static_check":
            raise FileNotFoundError("cppcheck")
        return super().static_check(source, board)

    def simulate(
        self,
        source: str,
        board: BoardProfile,
        compile_evidence: CommandEvidence,
    ) -> CommandEvidence:
        if self.missing_stage == "simulate":
            raise FileNotFoundError("simavr")
        return super().simulate(source, board, compile_evidence)


@pytest.mark.parametrize(
    "missing_stage, expected",
    [("compile", "compiler"), ("static_check", "static"), ("simulate", "simulator")],
)
def test_missing_verification_tool_is_unsupported(missing_stage: str, expected: str) -> None:
    result = ArduinoFirmwareWorkload().execute(
        _request(),
        _route(),
        _model_output(),
        MissingStageToolchain(missing_stage),
    )

    assert result.status is FirmwareStatus.UNSUPPORTED
    assert expected in " ".join(result.reasons)


@pytest.mark.parametrize(
    "evidence, expected",
    [
        (_evidence("wrong"), "stage identity"),
        (
            CommandEvidence(
                stage="compile",
                argv=(),
                exit_code=0,
                stdout="",
                stderr="",
                tool_version="compiler-1",
                artifact_sha256=hashlib.sha256(b"x").hexdigest(),
            ),
            "command",
        ),
        (
            CommandEvidence(
                stage="compile",
                argv=("arduino-cli", "upload"),
                exit_code=0,
                stdout="",
                stderr="",
                tool_version="compiler-1",
                artifact_sha256=hashlib.sha256(b"x").hexdigest(),
            ),
            "command",
        ),
        (
            CommandEvidence(
                stage="compile",
                argv=("compile",),
                exit_code=0,
                stdout="",
                stderr="",
                tool_version="",
                artifact_sha256=hashlib.sha256(b"x").hexdigest(),
            ),
            "tool version",
        ),
    ],
)
def test_evidence_identity_command_and_version_are_required(
    evidence: CommandEvidence,
    expected: str,
) -> None:
    problem = ArduinoFirmwareWorkload._check_evidence(
        evidence,
        "compile",
        require_digest=True,
    )
    assert expected in problem


def test_subprocess_toolchain_uses_board_qualified_non_upload_commands(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], cwd: Path, timeout_s: float) -> CommandEvidence:
        calls.append(argv)
        stage = "compile" if argv[:2] == ("arduino-cli", "compile") else "static_check"
        if argv[0] == "simavr":
            stage = "simulate"
        digest = hashlib.sha256(b"elf").hexdigest() if stage == "compile" else None
        return CommandEvidence(
            stage=stage,
            argv=argv,
            exit_code=0,
            stdout="GLUDD_OK" if stage == "simulate" else "ok",
            stderr="",
            tool_version="test-1",
            artifact_sha256=digest,
        )

    toolchain = SubprocessArduinoToolchain(workspace=tmp_path, runner=runner)
    board = supported_board("arduino:avr:uno")
    compile_evidence = toolchain.compile(VALID_SOURCE, board)
    toolchain.static_check(VALID_SOURCE, board)
    toolchain.simulate(VALID_SOURCE, board, compile_evidence)

    assert calls[0][:4] == (
        "arduino-cli",
        "compile",
        "--fqbn",
        "arduino:avr:uno",
    )
    assert calls[1][0] == "cppcheck"
    assert "--library=avr" in calls[1]
    assert calls[2][:5] == (
        "simavr",
        "-m",
        "atmega328p",
        "-f",
        "16000000",
    )
    assert all("upload" not in token for argv in calls for token in argv)


def test_subprocess_compile_hashes_the_real_elf_artifact(tmp_path: Path) -> None:
    def runner(argv: tuple[str, ...], cwd: Path, timeout_s: float) -> CommandEvidence:
        del timeout_s
        build = cwd / "build"
        build.mkdir(exist_ok=True)
        (build / "firmware.elf").write_bytes(b"real-elf")
        return CommandEvidence(
            stage="compile",
            argv=argv,
            exit_code=0,
            stdout="ok",
            stderr="",
            tool_version="arduino-cli-1",
        )

    evidence = SubprocessArduinoToolchain(workspace=tmp_path, runner=runner).compile(
        VALID_SOURCE,
        supported_board("arduino:avr:uno"),
    )

    assert evidence.artifact_sha256 == hashlib.sha256(b"real-elf").hexdigest()


def test_subprocess_helpers_classify_stages_versions_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    toolchain = SubprocessArduinoToolchain
    assert toolchain._infer_stage(("arduino-cli", "compile")) == "compile"
    assert toolchain._infer_stage(("cppcheck",)) == "static_check"
    assert toolchain._infer_stage(("simavr",)) == "simulate"

    calls = 0

    def run_timeout(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("simavr", 1.0, output=b"GLUDD_OK", stderr=None)
        return SimpleNamespace(returncode=0, stdout="simavr 1.7\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run_timeout)
    evidence = toolchain._run(("simavr", "firmware.elf"), tmp_path, 0.01)
    assert evidence.exit_code == 0
    assert evidence.bounded_termination is True
    assert evidence.stdout == "GLUDD_OK"
    assert evidence.tool_version == "simavr 1.7"


def test_subprocess_version_and_timeout_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def unavailable(*args: object, **kwargs: object) -> SimpleNamespace:
        raise OSError("missing")

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert SubprocessArduinoToolchain._version("missing", tmp_path) == "unknown"
    assert SubprocessArduinoToolchain._as_text(None) == ""
    assert SubprocessArduinoToolchain._as_text("text") == "text"


def test_embedded_domain_has_no_self_improvement_dependency() -> None:
    source = Path("src/general_ludd/embedded/firmware.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any("self_improve" in name for name in imports)
