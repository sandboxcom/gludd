"""Runtime validator for native and container deployment modes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_harness.runtime.profile import DataSourceMount, RuntimeProfile


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class MountValidationResult:
    mount_id: str
    valid: bool
    errors: list[str] = field(default_factory=list)


class RuntimeValidator:
    def validate_profile(self, profile: RuntimeProfile) -> dict[str, Any]:
        issues: list[str] = []
        if profile.mode not in ("native_uv", "native_pip", "container"):
            issues.append(f"Invalid mode: {profile.mode}")
        for mount in profile.mounts:
            if mount.required and mount.source_type == "bind" and mount.host_path is None:
                issues.append(f"Required bind mount {mount.mount_id} missing host_path")
            if not mount.container_path.startswith("/"):
                issues.append(f"Container path must be absolute: {mount.container_path}")
        return {"valid": len(issues) == 0, "issues": issues}

    def validate_native_uv(self, profile: RuntimeProfile) -> ValidationResult:
        errors: list[str] = []
        if profile.mode != "native_uv":
            errors.append(f"Expected mode native_uv, got {profile.mode}")
            return ValidationResult(valid=False, errors=errors)
        try:
            result = subprocess.run(
                ["uv", "sync", "--dry-run", "--project", profile.project_root],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                errors.append(f"uv sync check failed: {result.stderr.strip()}")
        except FileNotFoundError:
            errors.append("uv not found on PATH")
        except subprocess.TimeoutExpired:
            errors.append("uv sync check timed out")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def validate_native_pip(self, profile: RuntimeProfile) -> ValidationResult:
        errors: list[str] = []
        if profile.mode != "native_pip":
            errors.append(f"Expected mode native_pip, got {profile.mode}")
            return ValidationResult(valid=False, errors=errors)
        try:
            result = subprocess.run(
                ["pip", "install", "--dry-run", "-e", profile.project_root],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                errors.append(f"pip install check failed: {result.stderr.strip()}")
        except FileNotFoundError:
            errors.append("pip not found on PATH")
        except subprocess.TimeoutExpired:
            errors.append("pip install check timed out")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def validate_container(self, profile: RuntimeProfile) -> ValidationResult:
        errors: list[str] = []
        if profile.mode != "container":
            errors.append(f"Expected mode container, got {profile.mode}")
            return ValidationResult(valid=False, errors=errors)
        if not profile.config_path:
            errors.append("Container mode requires an image reference in config_path")
        for mount in profile.mounts:
            if mount.required and mount.source_type == "bind" and mount.host_path is None:
                errors.append(f"Required bind mount {mount.mount_id} missing host_path")
            if not mount.container_path.startswith("/"):
                errors.append(f"Container path must be absolute: {mount.container_path}")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def validate_data_source_mounts(
        self, mounts: list[DataSourceMount]
    ) -> list[MountValidationResult]:
        results: list[MountValidationResult] = []
        for mount in mounts:
            errors: list[str] = []
            if not mount.container_path.startswith("/"):
                errors.append(f"Container path must be absolute: {mount.container_path}")
            if mount.source_type == "bind" and mount.host_path is not None and not Path(mount.host_path).exists():
                errors.append(f"Bind source does not exist: {mount.host_path}")
            if mount.source_type == "bind" and mount.host_path is None and mount.required:
                errors.append(f"Required bind mount {mount.mount_id} has no host_path")
            if mount.source_type == "named_volume" and not mount.volume_name:
                errors.append(f"Named volume mount {mount.mount_id} missing volume_name")
            results.append(
                MountValidationResult(
                    mount_id=mount.mount_id,
                    valid=len(errors) == 0,
                    errors=errors,
                )
            )
        return results
