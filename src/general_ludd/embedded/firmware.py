"""Board-qualified Arduino firmware generation and verification.

The model only proposes a structured candidate. Completion belongs to this
deterministic workload adapter and requires independent compilation, static
analysis, and simulator evidence. The default path never uploads to hardware.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

_SCHEMA_VERSION = "1.0"
_LANGUAGE = "arduino-cpp"
_CAPABILITY = "arduino-cpp"
_HEALTHY_STATES = frozenset({"healthy", "ready"})
_BACKENDS = frozenset({"local", "azure"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRYPOINT_RE = re.compile(r"\bvoid\s+(setup|loop)\s*\(")
_PLACEHOLDER_RE = re.compile(
    r"\b(todo|fixme|placeholder|implementation\s+omitted|not\s+implemented|lorem\s+ipsum)\b",
    re.IGNORECASE,
)
_UNSAFE_RE = re.compile(
    r"(?:\bsystem\s*\(|\bexec\w*\s*\(|\bpopen\s*\(|\bfork\s*\(|"
    r"\bavrdude\b|\barduino-cli\s+upload\b|`|\$\()",
    re.IGNORECASE,
)
_MAX_SOURCE_BYTES = 256_000


@dataclass(frozen=True)
class BoardProfile:
    """Pinned compiler and simulator identity for one supported board."""

    fqbn: str
    mcu: str
    clock_hz: int
    simulator: str
    flash_bytes: int
    ram_bytes: int


_SUPPORTED_BOARDS: dict[str, BoardProfile] = {
    "arduino:avr:uno": BoardProfile(
        fqbn="arduino:avr:uno",
        mcu="atmega328p",
        clock_hz=16_000_000,
        simulator="simavr",
        flash_bytes=32_256,
        ram_bytes=2_048,
    ),
    "arduino:avr:nano": BoardProfile(
        fqbn="arduino:avr:nano",
        mcu="atmega328p",
        clock_hz=16_000_000,
        simulator="simavr",
        flash_bytes=30_720,
        ram_bytes=2_048,
    ),
    "arduino:avr:mega": BoardProfile(
        fqbn="arduino:avr:mega",
        mcu="atmega2560",
        clock_hz=16_000_000,
        simulator="simavr",
        flash_bytes=253_952,
        ram_bytes=8_192,
    ),
}


def supported_board(fqbn: str) -> BoardProfile:
    """Resolve an exact FQBN or fail closed for an unqualified target."""
    try:
        return _SUPPORTED_BOARDS[fqbn]
    except KeyError as exc:
        raise ValueError(f"unsupported board FQBN: {fqbn!r}") from exc


@dataclass(frozen=True)
class FirmwareRequest:
    """A bounded Arduino-class firmware objective."""

    request_id: str
    objective: str
    board_fqbn: str
    expected_serial: str
    physical_device_access: bool = False
    data_classification: str = "internal"


@dataclass(frozen=True)
class ModelRoute:
    """Auditable backend-selection evidence supplied by the universal router."""

    backend: str
    profile_id: str
    endpoint_id: str
    decision_id: str
    capability_evidence: tuple[str, ...]
    health_evidence: str
    cost_usd: float
    data_classification: str


@dataclass(frozen=True)
class CommandEvidence:
    """Immutable evidence from one external verification stage."""

    stage: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    tool_version: str
    artifact_sha256: str | None = None
    bounded_termination: bool = False


class FirmwareToolchain(Protocol):
    """Compiler/analyzer/simulator boundary used by the workload."""

    def compile(self, source: str, board: BoardProfile) -> CommandEvidence:
        """Compile source for the exact board and return artifact evidence."""

    def static_check(self, source: str, board: BoardProfile) -> CommandEvidence:
        """Run a static analyzer configured for the board family."""

    def simulate(
        self,
        source: str,
        board: BoardProfile,
        compile_evidence: CommandEvidence,
    ) -> CommandEvidence:
        """Run the compiled artifact without accessing a physical device."""


@dataclass(frozen=True)
class FirmwareCandidate:
    """Strict model-produced candidate; never completion evidence itself."""

    schema_version: str
    language: str
    board_fqbn: str
    source: str
    assumptions: tuple[str, ...] = field(default_factory=tuple)


class FirmwareStatus(StrEnum):
    """Terminal status for the evidence-gated workload."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    REFUSED = "refused"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class FirmwareResult:
    """Firmware outcome with evidence and reconstructable provenance."""

    request_id: str
    status: FirmwareStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[CommandEvidence, ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        """Only a fully verified success is complete."""
        return self.status is FirmwareStatus.SUCCEEDED


class ArduinoFirmwareWorkload:
    """Turn a model proposal into verified Arduino firmware or reject it."""

    def build_messages(self, request: FirmwareRequest) -> list[dict[str, str]]:
        """Build a backend-neutral generation request with a strict wire format."""
        board = supported_board(request.board_fqbn)
        contract = {
            "schema_version": _SCHEMA_VERSION,
            "language": _LANGUAGE,
            "board_fqbn": board.fqbn,
            "source": "complete .ino source",
            "assumptions": ["explicit hardware assumptions"],
        }
        return [
            {
                "role": "system",
                "content": (
                    "Generate complete Arduino firmware as strict JSON only; "
                    "do not emit Markdown or prose. The firmware must compile and "
                    "simulate for the exact FQBN. It must not upload to or access "
                    "a physical device. Do not claim completion."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Objective: {request.objective}\n"
                    f"Board FQBN: {board.fqbn}\n"
                    f"Simulator observable: serial output containing {request.expected_serial!r}\n"
                    f"Required strict JSON shape: {json.dumps(contract, sort_keys=True)}\n"
                    "The result will be independently compiled, statically checked, and simulated."
                ),
            },
        ]

    def execute(
        self,
        request: FirmwareRequest,
        route: ModelRoute,
        model_output: str,
        toolchain: FirmwareToolchain,
    ) -> FirmwareResult:
        """Validate, compile, analyze, and simulate a model candidate."""
        refusal = self._route_refusal(request, route)
        if refusal:
            return self._result(request, FirmwareStatus.REFUSED, refusal)

        try:
            board = supported_board(request.board_fqbn)
        except ValueError as exc:
            return self._result(request, FirmwareStatus.UNSUPPORTED, str(exc))

        try:
            candidate = self._decode_candidate(model_output, board)
        except ValueError as exc:
            return self._result(request, FirmwareStatus.REJECTED, str(exc))

        evidence: list[CommandEvidence] = []
        try:
            compiled = toolchain.compile(candidate.source, board)
        except FileNotFoundError as exc:
            return self._result(request, FirmwareStatus.UNSUPPORTED, f"compiler unavailable: {exc}")
        except Exception as exc:  # pragma: no cover - defensive plugin boundary
            return self._result(request, FirmwareStatus.FAILED, f"compiler execution failed: {exc}")
        evidence.append(compiled)
        problem = self._check_evidence(compiled, "compile", require_digest=True)
        if problem:
            return self._result(request, FirmwareStatus.REJECTED, problem, evidence)

        try:
            static = toolchain.static_check(candidate.source, board)
        except FileNotFoundError as exc:
            return self._result(
                request,
                FirmwareStatus.UNSUPPORTED,
                f"static analyzer unavailable: {exc}",
                evidence,
            )
        except Exception as exc:  # pragma: no cover - defensive plugin boundary
            return self._result(request, FirmwareStatus.FAILED, f"static analysis failed: {exc}", evidence)
        evidence.append(static)
        problem = self._check_evidence(static, "static_check")
        if problem:
            return self._result(request, FirmwareStatus.REJECTED, problem, evidence)

        try:
            simulated = toolchain.simulate(candidate.source, board, compiled)
        except FileNotFoundError as exc:
            return self._result(
                request,
                FirmwareStatus.UNSUPPORTED,
                f"simulator unavailable: {exc}",
                evidence,
            )
        except Exception as exc:  # pragma: no cover - defensive plugin boundary
            return self._result(request, FirmwareStatus.FAILED, f"simulation failed: {exc}", evidence)
        evidence.append(simulated)
        problem = self._check_evidence(simulated, "simulate")
        if problem:
            return self._result(request, FirmwareStatus.REJECTED, problem, evidence)
        if request.expected_serial not in simulated.stdout:
            return self._result(
                request,
                FirmwareStatus.REJECTED,
                "simulator did not produce the expected serial observable",
                evidence,
            )

        provenance = {
            "schema_version": _SCHEMA_VERSION,
            "request_id": request.request_id,
            "board": asdict(board),
            "route": asdict(route),
            "source_sha256": hashlib.sha256(candidate.source.encode("utf-8")).hexdigest(),
            "compile_artifact_sha256": compiled.artifact_sha256,
            "tools": [
                {
                    "stage": item.stage,
                    "version": item.tool_version,
                    "argv": list(item.argv),
                }
                for item in evidence
            ],
        }
        return FirmwareResult(
            request_id=request.request_id,
            status=FirmwareStatus.SUCCEEDED,
            evidence=tuple(evidence),
            provenance=provenance,
        )

    @staticmethod
    def _result(
        request: FirmwareRequest,
        status: FirmwareStatus,
        reason: str,
        evidence: list[CommandEvidence] | None = None,
    ) -> FirmwareResult:
        return FirmwareResult(
            request_id=request.request_id,
            status=status,
            reasons=(reason,),
            evidence=tuple(evidence or ()),
        )

    @staticmethod
    def _route_refusal(request: FirmwareRequest, route: ModelRoute) -> str:
        if request.physical_device_access:
            return "physical device access requires a separate approved deployment workflow"
        if route.backend not in _BACKENDS:
            return f"unsupported model backend: {route.backend!r}"
        if route.health_evidence.lower() not in _HEALTHY_STATES:
            return "selected model endpoint is not healthy"
        capabilities = {item.lower() for item in route.capability_evidence}
        if _CAPABILITY not in capabilities:
            return "selected model lacks Arduino C++ capability evidence"
        if route.cost_usd < 0:
            return "route cost evidence is invalid"
        classification = request.data_classification.lower()
        if route.data_classification.lower() != classification:
            return "route data-classification evidence does not match the request"
        if classification == "restricted" and route.backend != "local":
            return "restricted firmware requests must remain on a local backend"
        if not all((route.profile_id, route.endpoint_id, route.decision_id)):
            return "route provenance is incomplete"
        return ""

    @staticmethod
    def _decode_candidate(raw: str, board: BoardProfile) -> FirmwareCandidate:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("model output must be strict JSON, not source text or Markdown") from exc
        if not isinstance(payload, dict):
            raise ValueError("model output must be a strict JSON object")
        required = {"schema_version", "language", "board_fqbn", "source", "assumptions"}
        missing = required - set(payload)
        unknown = set(payload) - required
        if missing:
            raise ValueError(f"candidate schema is missing fields: {sorted(missing)}")
        if unknown:
            raise ValueError(f"candidate schema contains unknown fields: {sorted(unknown)}")
        if payload["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("candidate schema version is unsupported")
        if payload["language"] != _LANGUAGE:
            raise ValueError("candidate language must be arduino-cpp")
        if payload["board_fqbn"] != board.fqbn:
            raise ValueError("candidate board FQBN does not match the requested board")
        source = payload["source"]
        assumptions = payload["assumptions"]
        if not isinstance(source, str) or not source.strip():
            raise ValueError("candidate source must be non-empty text")
        if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
            raise ValueError("candidate assumptions must be a JSON string array")
        ArduinoFirmwareWorkload._validate_source(source)
        return FirmwareCandidate(
            schema_version=_SCHEMA_VERSION,
            language=_LANGUAGE,
            board_fqbn=board.fqbn,
            source=source,
            assumptions=tuple(assumptions),
        )

    @staticmethod
    def _validate_source(source: str) -> None:
        if len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
            raise ValueError("candidate source exceeds the bounded size limit")
        if "```" in source:
            raise ValueError("candidate source must not contain Markdown fences")
        entrypoints = set(_ENTRYPOINT_RE.findall(source))
        if entrypoints != {"setup", "loop"}:
            raise ValueError("candidate source must define both setup() and loop()")
        if _PLACEHOLDER_RE.search(source):
            raise ValueError("candidate source contains a placeholder")
        if _UNSAFE_RE.search(source):
            raise ValueError("candidate source contains an unsafe host or upload operation")

    @staticmethod
    def _check_evidence(
        evidence: CommandEvidence,
        expected_stage: str,
        *,
        require_digest: bool = False,
    ) -> str:
        if evidence.stage != expected_stage:
            return f"{expected_stage} evidence has the wrong stage identity"
        if not evidence.argv or any("upload" in token.lower() for token in evidence.argv):
            return f"{expected_stage} evidence contains an unsafe or missing command"
        if evidence.exit_code != 0:
            return f"{expected_stage} verification failed with exit code {evidence.exit_code}"
        if not evidence.tool_version.strip():
            return f"{expected_stage} evidence is missing a tool version"
        if require_digest and (
            evidence.artifact_sha256 is None
            or _SHA256_RE.fullmatch(evidence.artifact_sha256) is None
        ):
            return "compile evidence is missing a valid artifact digest"
        return ""


Runner = Callable[[tuple[str, ...], Path, float], CommandEvidence]


class SubprocessArduinoToolchain:
    """Arduino CLI + Cppcheck + simavr implementation with list-form argv."""

    def __init__(
        self,
        *,
        workspace: Path,
        runner: Runner | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        """Initialize an isolated tool workspace and optional command runner."""
        self._workspace = workspace.resolve()
        self._runner = runner or self._run
        self._timeout_s = timeout_s
        self._sketch_dir = self._workspace / "gludd_firmware"
        self._source_path = self._sketch_dir / "gludd_firmware.ino"
        self._build_dir = self._workspace / "build"

    def compile(self, source: str, board: BoardProfile) -> CommandEvidence:
        """Compile a sketch for ``board`` and hash the emitted ELF artifact."""
        self._materialize(source)
        argv = (
            "arduino-cli",
            "compile",
            "--fqbn",
            board.fqbn,
            "--warnings",
            "all",
            "--output-dir",
            str(self._build_dir),
            str(self._sketch_dir),
        )
        evidence = self._runner(argv, self._workspace, self._timeout_s)
        if evidence.exit_code != 0 or evidence.artifact_sha256:
            return evidence
        artifact = self._find_artifact()
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact else None
        return CommandEvidence(
            stage=evidence.stage,
            argv=evidence.argv,
            exit_code=evidence.exit_code,
            stdout=evidence.stdout,
            stderr=evidence.stderr,
            tool_version=evidence.tool_version,
            artifact_sha256=digest,
            bounded_termination=evidence.bounded_termination,
        )

    def static_check(self, source: str, board: BoardProfile) -> CommandEvidence:
        """Analyze a sketch with Cppcheck's AVR model."""
        del board
        self._materialize(source)
        argv = (
            "cppcheck",
            "--enable=warning,style,performance,portability",
            "--error-exitcode=1",
            "--library=avr",
            "--language=c++",
            "--std=c++11",
            str(self._source_path),
        )
        return self._runner(argv, self._workspace, self._timeout_s)

    def simulate(
        self,
        source: str,
        board: BoardProfile,
        compile_evidence: CommandEvidence,
    ) -> CommandEvidence:
        """Run the compiled ELF in the board's offline simulator."""
        del source, compile_evidence
        artifact = self._find_artifact() or (self._build_dir / "gludd_firmware.ino.elf")
        argv = (
            board.simulator,
            "-m",
            board.mcu,
            "-f",
            str(board.clock_hz),
            str(artifact),
        )
        return self._runner(argv, self._workspace, self._timeout_s)

    def _materialize(self, source: str) -> None:
        self._sketch_dir.mkdir(parents=True, exist_ok=True)
        self._build_dir.mkdir(parents=True, exist_ok=True)
        self._source_path.write_text(source, encoding="utf-8")

    def _find_artifact(self) -> Path | None:
        artifacts = sorted(self._build_dir.glob("*.elf"))
        return artifacts[0] if artifacts else None

    @staticmethod
    def _infer_stage(argv: tuple[str, ...]) -> str:
        if argv[:2] == ("arduino-cli", "compile"):
            return "compile"
        if argv and argv[0] == "cppcheck":
            return "static_check"
        return "simulate"

    @staticmethod
    def _version(executable: str, cwd: Path) -> str:
        try:
            result = subprocess.run(
                [executable, "--version"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        output = (result.stdout or result.stderr).strip().splitlines()
        return output[0][:200] if output else "unknown"

    @classmethod
    def _run(cls, argv: tuple[str, ...], cwd: Path, timeout_s: float) -> CommandEvidence:
        stage = cls._infer_stage(argv)
        bounded = False
        try:
            result = subprocess.run(
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = cls._as_text(exc.stdout)
            stderr = cls._as_text(exc.stderr)
            bounded = True
            exit_code = 0 if stage == "simulate" and bool(stdout.strip()) else 124
        return CommandEvidence(
            stage=stage,
            argv=argv,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            tool_version=cls._version(argv[0], cwd),
            bounded_termination=bounded,
        )

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


__all__ = [
    "ArduinoFirmwareWorkload",
    "BoardProfile",
    "CommandEvidence",
    "FirmwareRequest",
    "FirmwareResult",
    "FirmwareStatus",
    "FirmwareToolchain",
    "ModelRoute",
    "SubprocessArduinoToolchain",
    "supported_board",
]
