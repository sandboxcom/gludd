"""Container builder for podman/docker image builds."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class BuildResult:
    image_ref: str
    image_digest: str
    success: bool
    logs: str = ""


@dataclass
class ImageValidationResult:
    valid: bool
    has_baked_state: bool
    entrypoint_correct: bool
    size_mb: float


class ContainerBuilder:
    def build_image(
        self,
        context_dir: str,
        image_ref: str,
        runtime: str = "podman",
    ) -> BuildResult:
        try:
            result = subprocess.run(
                [runtime, "build", "-t", image_ref, context_dir],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError:
            return BuildResult(
                image_ref=image_ref,
                image_digest="",
                success=False,
                logs=f"{runtime} not found on PATH",
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                image_ref=image_ref,
                image_digest="",
                success=False,
                logs="Build timed out",
            )

        if result.returncode != 0:
            return BuildResult(
                image_ref=image_ref,
                image_digest="",
                success=False,
                logs=result.stderr,
            )

        digest = ""
        for line in result.stdout.splitlines():
            if "sha256:" in line:
                digest = line.strip()

        return BuildResult(
            image_ref=image_ref,
            image_digest=digest,
            success=True,
            logs=result.stdout,
        )

    def validate_image(self, image_ref: str, runtime: str = "podman") -> ImageValidationResult:
        try:
            result = subprocess.run(
                [runtime, "inspect", image_ref],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ImageValidationResult(
                valid=False,
                has_baked_state=False,
                entrypoint_correct=False,
                size_mb=0.0,
            )

        if result.returncode != 0:
            return ImageValidationResult(
                valid=False,
                has_baked_state=False,
                entrypoint_correct=False,
                size_mb=0.0,
            )

        try:
            inspect_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ImageValidationResult(
                valid=False,
                has_baked_state=False,
                entrypoint_correct=False,
                size_mb=0.0,
            )

        info = inspect_data[0] if isinstance(inspect_data, list) and len(inspect_data) > 0 else inspect_data

        config = info.get("Config", info.get("config", {}))
        entrypoint = config.get("Entrypoint", [])
        entrypoint_correct = "hottentot" in " ".join(entrypoint)

        size_bytes = info.get("Size", info.get("size", 0))
        if isinstance(size_bytes, str):
            try:
                size_bytes = int(size_bytes)
            except ValueError:
                size_bytes = 0
        size_mb = float(size_bytes) / (1024 * 1024)

        env_list = config.get("Env", [])
        env_str = " ".join(env_list) if isinstance(env_list, list) else str(env_list)
        has_baked_state = any(
            kw in env_str.lower() for kw in ["password", "secret", "token", "api_key"]
        )

        return ImageValidationResult(
            valid=True,
            has_baked_state=has_baked_state,
            entrypoint_correct=entrypoint_correct,
            size_mb=size_mb,
        )
