"""Release orchestrator — build the pip bundle + container, then validate.

Ties together PipBundleBuilder, ContainerBuilder, and ReleaseArtifactValidator
into one production entry point (driven by ``make release-validate``) so the
build/validate classes live on a real call path. Returns a serializable report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from general_ludd.runtime.container import ContainerBuilder
from general_ludd.runtime.manifest_signer import ManifestSigner
from general_ludd.runtime.pip_bundle import PipBundleBuilder
from general_ludd.runtime.release import ReleaseArtifactValidator


def build_and_validate_release(
    version: str,
    output_dir: str,
    build_container: bool = False,
    context_dir: str = ".",
    image_ref: str | None = None,
    container_runtime: str = "podman",
    allowed_signers_path: str | None = None,
) -> dict[str, Any]:
    """Build the pip bundle (and optionally a container), then validate."""
    bundle = PipBundleBuilder().build(output_dir=output_dir, version=version)
    trust_store = allowed_signers_path or str(
        Path(output_dir) / "release.allowed_signers"
    )
    if bundle.success and bundle.signature_valid:
        signer = ManifestSigner()
        public_key_path = Path(f"{signer.private_key_path}.pub")
        if public_key_path.exists():
            signer.make_allowed_signers(
                "release-bundle", public_key_path.read_text().strip(), trust_store
            )
    report: dict[str, Any] = {
        "version": version,
        "bundle": {
            "success": bundle.success,
            "wheel_path": bundle.wheel_path,
            "sdist_path": bundle.sdist_path,
            "manifest_path": bundle.manifest_path,
            "sig_path": bundle.sig_path,
            "signature_valid": bundle.signature_valid,
        },
        "container": None,
    }

    if build_container:
        ref = image_ref or f"gl-agent:{version}"
        build = ContainerBuilder().build_image(
            context_dir=context_dir, image_ref=ref, runtime=container_runtime
        )
        report["container"] = {
            "success": build.success,
            "image_ref": build.image_ref,
            "image_digest": build.image_digest,
        }

    validation = ReleaseArtifactValidator(allowed_signers_path=trust_store).validate_release(
        version=version, artifacts_dir=output_dir, require_container=build_container
    )
    report["validation"] = {
        "valid": validation.valid,
        "pip_bundle_valid": validation.pip_bundle_valid,
        "container_valid": validation.container_valid,
        "manifest_valid": validation.manifest_valid,
        "signature_valid": validation.signature_valid,
        "errors": validation.errors,
    }
    return report
