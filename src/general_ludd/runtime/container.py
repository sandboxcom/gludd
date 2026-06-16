"""Container builder for podman/docker image builds."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


class ImageRefError(ValueError):
    """Raised when an image reference fails injection-hardening validation."""


class RuntimeNameError(ValueError):
    """Raised when a container runtime name fails validation."""


# Allowed characters for an OCI image reference: registry host, port, path,
# tag and digest. Deliberately excludes whitespace and every shell metachar
# (; & | ` $ ( ) < > ' " \\ newline/tab) so the value can never inject when it
# eventually reaches argv. No leading '-' so it can't be parsed as a flag.
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")

# Runtime is an executable name resolved on PATH (podman/docker/nerdctl).
_RUNTIME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_image_ref(image_ref: str) -> str:
    """Return ``image_ref`` unchanged if safe, else raise ``ImageRefError``.

    Rejects empty/whitespace-only values, leading dashes (flag injection) and
    any shell metacharacter or whitespace.
    """
    if not isinstance(image_ref, str) or not image_ref.strip():
        raise ImageRefError("image reference must be a non-empty string")
    if image_ref.startswith("-"):
        raise ImageRefError(f"image reference must not start with '-': {image_ref!r}")
    if not _IMAGE_REF_RE.fullmatch(image_ref):
        raise ImageRefError(f"image reference contains illegal characters: {image_ref!r}")
    return image_ref


def _validate_runtime_name(runtime: str) -> str:
    """Return ``runtime`` unchanged if safe, else raise ``RuntimeNameError``."""
    if not isinstance(runtime, str) or not runtime.strip():
        raise RuntimeNameError("runtime name must be a non-empty string")
    if runtime.startswith("-"):
        raise RuntimeNameError(f"runtime name must not start with '-': {runtime!r}")
    if not _RUNTIME_NAME_RE.fullmatch(runtime):
        raise RuntimeNameError(f"runtime name contains illegal characters: {runtime!r}")
    return runtime


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
            safe_runtime = _validate_runtime_name(runtime)
            safe_image_ref = _validate_image_ref(image_ref)
        except (ImageRefError, RuntimeNameError) as exc:
            return BuildResult(
                image_ref=image_ref,
                image_digest="",
                success=False,
                logs=f"rejected unsafe build args: {exc}",
            )
        try:
            result = subprocess.run(
                # ``--`` ends option parsing so the positional context_dir can
                # never be mistaken for a flag.
                [safe_runtime, "build", "-t", safe_image_ref, "--", context_dir],
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
            safe_runtime = _validate_runtime_name(runtime)
            safe_image_ref = _validate_image_ref(image_ref)
        except (ImageRefError, RuntimeNameError):
            return ImageValidationResult(
                valid=False,
                has_baked_state=False,
                entrypoint_correct=False,
                size_mb=0.0,
            )
        try:
            result = subprocess.run(
                # ``--`` ends option parsing so the positional image ref can
                # never be mistaken for a flag.
                [safe_runtime, "inspect", "--", safe_image_ref],
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
        entrypoint_correct = "gludd" in " ".join(entrypoint)

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
