"""Direct, bounded Terraform/OpenTofu execution for owned Azure resources."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import IO, Any

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.infra.azure_containerapp_make_types import (
    MakeRuntimeState as TerraformRuntimeState,
)

OWNERSHIP_MARKER = ".gludd-azure-containerapp-live-proof.json"
OWNERSHIP_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
TERRAFORM_PHASES = (
    "init",
    "validate",
    "plan",
    "show-plan",
    "apply",
    "output",
    "destroy",
)
_JSON_PHASES = frozenset({"show-plan", "output"})
_PLAN_REQUIRED_PHASES = frozenset({"show-plan", "apply"})
_MAX_MARKER_BYTES = 4096
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_DIAGNOSTIC_BYTES = 256 * 1024
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INHERITED_ENVIRONMENT_NAMES = frozenset(
    {
        "APPDATA",
        "CURL_CA_BUNDLE",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LOCALAPPDATA",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
    }
)

ProgressSink = Callable[[str, TerraformRuntimeState, int], None]


class AzureContainerAppTerraformPhaseError(RuntimeError):
    """Content-free failure from one direct Terraform/OpenTofu phase."""

    def __init__(self, phase: str, failure_class: str = "internal") -> None:
        """Retain only the bounded phase name, never provider output."""
        super().__init__(f"Azure Container App Terraform phase failed: {phase}")
        self.phase = phase
        self.failure_class = failure_class


def _default_binary_resolver() -> str:
    return BinaryPathResolver().get_infra_binary()


def _discard_progress(
    _phase: str,
    _state: TerraformRuntimeState,
    _elapsed_seconds: int,
) -> None:
    return None


def _regular_file(path: Path, *, required: bool = True) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return not required
    return stat.S_ISREG(metadata.st_mode)


def _validate_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise ValueError
    result: dict[str, str] = {}
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or _ENVIRONMENT_NAME.fullmatch(key) is None
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise ValueError
        result[key] = value
    return result


def terraform_process_environment(
    overrides: Mapping[str, str],
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal child environment without unrelated project secrets."""
    parent = os.environ if parent_environment is None else parent_environment
    inherited = {
        key: value
        for key, value in parent.items()
        if key in _INHERITED_ENVIRONMENT_NAMES
    }
    inherited.update(overrides)
    return _validate_environment(inherited)


def _validate_owned_paths(
    terraform_dir: Path,
    plan_file: Path,
    json_file: Path,
    allowed_root: Path,
    phase: str,
) -> tuple[Path, Path, Path]:
    resolved_root = allowed_root.resolve()
    resolved_dir = terraform_dir.resolve()
    resolved_plan = plan_file.resolve()
    resolved_json = json_file.resolve()
    if (
        resolved_dir.parent != resolved_root
        or re.fullmatch(r"[0-9a-f]{24}", resolved_dir.name) is None
        or not resolved_dir.is_dir()
        or terraform_dir.is_symlink()
    ):
        raise ValueError
    if resolved_plan.parent != resolved_dir or resolved_plan.name != "gludd.tfplan":
        raise ValueError
    if (
        resolved_json.parent != resolved_dir
        or resolved_json.name not in {"gludd.plan.json", "gludd.output.json"}
    ):
        raise ValueError
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        if not _regular_file(resolved_dir / name):
            raise ValueError
    marker_path = resolved_dir / OWNERSHIP_MARKER
    if not _regular_file(marker_path):
        raise ValueError
    marker_raw = marker_path.read_bytes()
    if not marker_raw or len(marker_raw) > _MAX_MARKER_BYTES:
        raise ValueError
    marker = json.loads(marker_raw.decode("utf-8"))
    if (
        not isinstance(marker, dict)
        or set(marker) != {"operation_digest", "protocol"}
        or marker.get("protocol") != OWNERSHIP_PROTOCOL
        or not isinstance(marker.get("operation_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", marker["operation_digest"]) is None
        or not marker["operation_digest"].startswith(resolved_dir.name)
    ):
        raise ValueError
    if phase in _PLAN_REQUIRED_PHASES and (
        not _regular_file(resolved_plan) or resolved_plan.stat().st_size <= 0
    ):
        raise ValueError
    return resolved_dir, resolved_plan, resolved_json


def _command(phase: str, binary: str, plan_file: Path) -> list[str]:
    commands = {
        "init": [binary, "init", "-backend=false", "-input=false", "-no-color"],
        "validate": [binary, "validate", "-no-color"],
        "plan": [
            binary,
            "plan",
            "-input=false",
            "-no-color",
            f"-out={plan_file}",
        ],
        "show-plan": [binary, "show", "-json", str(plan_file)],
        "apply": [
            binary,
            "apply",
            "-input=false",
            "-no-color",
            "-auto-approve",
            str(plan_file),
        ],
        "output": [binary, "output", "-json"],
        "destroy": [
            binary,
            "destroy",
            "-input=false",
            "-no-color",
            "-auto-approve",
        ],
    }
    return commands[phase]


def _terminate(process: Any) -> None:
    try:
        if process.poll() is not None:
            return
        process.terminate()
        process.wait(timeout=5.0)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5.0)
        except Exception:
            return


def _classify_provider_failure(output: IO[str]) -> str:
    """Map bounded provider output to a fixed class without retaining its text."""
    try:
        output.flush()
        output.seek(0, os.SEEK_END)
        size = output.tell()
        output.seek(max(0, size - _MAX_DIAGNOSTIC_BYTES))
        normalized = output.read(_MAX_DIAGNOSTIC_BYTES).casefold()
    except Exception:
        return "provider"
    classes = (
        (("authorizationfailed", "403 forbidden", "statuscode=403"), "authorization"),
        (
            (
                "missingsubscriptionregistration",
                "not registered to use namespace",
                "noregisteredproviderfound",
            ),
            "provider-registration",
        ),
        (
            ("resourcegroupnotfound", "resource group could not be found"),
            "resource-group-not-found",
        ),
        (
            ("quotaexceeded", "operation could not be completed as it results in exceeding"),
            "quota",
        ),
        (("invalidworkloadprofiletype", "workload profile type"), "workload-profile"),
        (("locationnotavailableforresourcetype",), "location"),
    )
    for markers, failure_class in classes:
        if any(marker in normalized for marker in markers):
            return failure_class
    return "provider"


@contextmanager
def _diagnostic_output() -> Iterator[IO[str]]:
    """Yield a private ephemeral stream that is always unlinked on close."""
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        yield output


class AzureContainerAppTerraformPhaseExecutor:
    """Run one owned Terraform phase directly without Make or a shell."""

    def __init__(
        self,
        *,
        binary_resolver: Callable[[], str] = _default_binary_resolver,
        process_factory: Callable[..., Any] = subprocess.Popen,
        heartbeat_seconds: float = 10.0,
        poll_seconds: float = 0.25,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize bounded process, timing, and binary-resolution seams."""
        if not callable(binary_resolver) or not callable(process_factory):
            raise ValueError("Terraform execution boundaries must be callable")
        if not callable(monotonic) or not callable(sleeper):
            raise ValueError("Terraform timing boundaries must be callable")
        for value, name in (
            (heartbeat_seconds, "heartbeat_seconds"),
            (poll_seconds, "poll_seconds"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or float(value) <= 0
            ):
                raise ValueError(f"{name} must be positive")
        self._binary_resolver = binary_resolver
        self._process_factory = process_factory
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._poll_seconds = float(poll_seconds)
        self._monotonic = monotonic
        self._sleeper = sleeper

    def run(
        self,
        *,
        phase: str,
        terraform_dir: str | os.PathLike[str],
        plan_file: str | os.PathLike[str],
        json_file: str | os.PathLike[str],
        allowed_root: str | os.PathLike[str],
        environment: Mapping[str, str],
        timeout_seconds: int,
        progress: ProgressSink = _discard_progress,
    ) -> None:
        """Validate ownership, execute list argv, and emit content-free progress."""
        process: Any | None = None
        resource_stack = ExitStack()
        output_handle: IO[str] | None = None
        diagnostic_handle: IO[str] | None = None
        temporary_json: Path | None = None
        started: float | None = None
        failure_class = "internal"
        try:
            if phase not in TERRAFORM_PHASES:
                raise ValueError
            if (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, int)
                or not 0 < timeout_seconds <= 86_400
                or not callable(progress)
            ):
                raise ValueError
            resolved_dir, resolved_plan, resolved_json = _validate_owned_paths(
                Path(terraform_dir),
                Path(plan_file),
                Path(json_file),
                Path(allowed_root),
                phase,
            )
            process_environment = _validate_environment(environment)
            binary = self._binary_resolver()
            if not isinstance(binary, str) or not binary or "\x00" in binary:
                raise ValueError
            if phase in _JSON_PHASES:
                temporary_json = resolved_json.with_name(
                    f".{resolved_json.name}.{os.getpid()}.tmp"
                )
                temporary_json.unlink(missing_ok=True)
                descriptor = os.open(
                    temporary_json,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                output_handle = os.fdopen(descriptor, "w", encoding="utf-8")
            diagnostic_handle = resource_stack.enter_context(_diagnostic_output())
            started = self._monotonic()
            progress(phase, TerraformRuntimeState.STARTED, 0)
            process = self._process_factory(
                _command(phase, binary, resolved_plan),
                cwd=str(resolved_dir),
                env=process_environment,
                stdin=subprocess.DEVNULL,
                stdout=(output_handle if output_handle is not None else diagnostic_handle),
                stderr=(diagnostic_handle if output_handle is not None else subprocess.STDOUT),
                text=True,
                close_fds=True,
                shell=False,
            )
            last_heartbeat = started
            while True:
                returncode = process.poll()
                if returncode is not None:
                    break
                now = self._monotonic()
                elapsed = now - started
                if elapsed >= timeout_seconds:
                    failure_class = "timeout"
                    _terminate(process)
                    raise TimeoutError
                if now - last_heartbeat >= self._heartbeat_seconds:
                    progress(
                        phase,
                        TerraformRuntimeState.HEARTBEAT,
                        max(0, int(elapsed)),
                    )
                    last_heartbeat = now
                self._sleeper(self._poll_seconds)
            if output_handle is not None:
                output_handle.close()
                output_handle = None
            if int(returncode) != 0:
                failure_class = _classify_provider_failure(diagnostic_handle)
                raise RuntimeError
            if temporary_json is not None:
                if (
                    not _regular_file(temporary_json)
                    or temporary_json.stat().st_size > _MAX_JSON_BYTES
                ):
                    raise ValueError
                os.replace(temporary_json, resolved_json)
                temporary_json = None
            progress(
                phase,
                TerraformRuntimeState.SUCCEEDED,
                max(0, int(self._monotonic() - started)),
            )
        except KeyboardInterrupt:
            failure_class = "interrupted"
            if process is not None:
                _terminate(process)
            if started is not None:
                with suppress(Exception):
                    progress(
                        f"{phase}:{failure_class}",
                        TerraformRuntimeState.FAILED,
                        max(0, int(self._monotonic() - started)),
                    )
            raise AzureContainerAppTerraformPhaseError(phase, failure_class) from None
        except Exception:
            if process is not None:
                _terminate(process)
            if started is not None:
                with suppress(Exception):
                    progress(
                        f"{phase}:{failure_class}",
                        TerraformRuntimeState.FAILED,
                        max(0, int(self._monotonic() - started)),
                    )
            raise AzureContainerAppTerraformPhaseError(phase, failure_class) from None
        finally:
            if output_handle is not None:
                output_handle.close()
            if temporary_json is not None:
                temporary_json.unlink(missing_ok=True)
            resource_stack.close()


__all__ = (
    "OWNERSHIP_MARKER",
    "OWNERSHIP_PROTOCOL",
    "TERRAFORM_PHASES",
    "AzureContainerAppTerraformPhaseError",
    "AzureContainerAppTerraformPhaseExecutor",
    "TerraformRuntimeState",
    "terraform_process_environment",
)
