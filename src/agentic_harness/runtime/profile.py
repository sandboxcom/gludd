"""Runtime profile management module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DataSourceMount(BaseModel):
    mount_id: str
    purpose: str = "config"
    required: bool = True
    source_type: str = "bind"
    host_path: str | None = None
    volume_name: str | None = None
    container_path: str = "/data"
    native_path: str | None = None
    access: str = "ro"
    create_if_missing: bool = False
    secret_safe: bool = False
    model_visible: bool = False


class RuntimeProfile(BaseModel):
    runtime_profile_id: str
    mode: str = "native_uv"
    enabled: bool = True
    project_root: str = "."
    config_path: str | None = None
    python_version_constraint: str | None = None
    entrypoint: str | None = None
    healthcheck_url: str = "http://localhost:8000/healthz"
    required_services: list[str] = Field(default_factory=lambda: ["postgres"])
    mounts: list[DataSourceMount] = Field(default_factory=list)


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
