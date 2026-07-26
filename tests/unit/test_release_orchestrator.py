"""Tests for build_and_validate_release (runtime/release_orchestrator.py).

PipBundleBuilder / ContainerBuilder / ReleaseArtifactValidator are faked via
unittest.mock.patch on the orchestrator module's imported names so no real
subprocess (uv build / podman build) ever runs.

IMPORTANT (documented real contract, not weakened here): the orchestrator does
NOT short-circuit on failure. A failed bundle build still proceeds to build
the container (if requested) and always runs validation; a failed container
build still proceeds to validation. Only the collected report reflects the
failures. See build_and_validate_release in
src/general_ludd/runtime/release_orchestrator.py.

Run: make test-iso TESTFILE='tests/unit/test_release_orchestrator.py'
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from general_ludd.runtime.container import BuildResult
from general_ludd.runtime.pip_bundle import BundleResult
from general_ludd.runtime.release import ReleaseValidationResult
from general_ludd.runtime.release_orchestrator import build_and_validate_release


def _bundle_result(success: bool = True, **overrides) -> BundleResult:
    defaults = dict(
        bundle_path="/out",
        wheel_path="/out/pkg-1.0.0-py3-none-any.whl",
        sdist_path="/out/pkg-1.0.0.tar.gz",
        manifest_path="/out/MANIFEST.json",
        checksum_path="/out/CHECKSUMS.sha256",
        success=success,
    )
    defaults.update(overrides)
    return BundleResult(**defaults)


def _build_result(success: bool = True, **overrides) -> BuildResult:
    defaults = dict(
        image_ref="gl-agent:1.0.0",
        image_digest="sha256:deadbeef",
        success=success,
        logs="",
    )
    defaults.update(overrides)
    return BuildResult(**defaults)


def _validation_result(valid: bool = True, **overrides) -> ReleaseValidationResult:
    defaults = dict(
        valid=valid,
        pip_bundle_valid=valid,
        container_valid=valid,
        manifest_valid=valid,
        signature_valid=False,
        errors=[],
    )
    defaults.update(overrides)
    return ReleaseValidationResult(**defaults)


class TestBuildAndValidateReleasePipOnlyHappyPath:
    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_pip_only_container_not_called_exact_report(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result(
            wheel_path="/out/w.whl",
            sdist_path="/out/s.tar.gz",
            manifest_path="/out/MANIFEST.json",
        )
        mock_validator_cls.return_value.validate_release.return_value = _validation_result()

        report = build_and_validate_release(version="1.0.0", output_dir="/out")

        mock_container_cls.return_value.build_image.assert_not_called()
        mock_pip_cls.return_value.build.assert_called_once_with(output_dir="/out", version="1.0.0")
        mock_validator_cls.assert_called_once_with(allowed_signers_path="/out/release.allowed_signers")
        mock_validator_cls.return_value.validate_release.assert_called_once_with(
            version="1.0.0", artifacts_dir="/out", require_container=False
        )
        assert report == {
            "version": "1.0.0",
            "bundle": {
                "success": True,
                "wheel_path": "/out/w.whl",
                "sdist_path": "/out/s.tar.gz",
                "manifest_path": "/out/MANIFEST.json",
                "sig_path": "",
                "signature_valid": False,
            },
            "container": None,
            "validation": {
                "valid": True,
                "pip_bundle_valid": True,
                "container_valid": True,
                "manifest_valid": True,
                "signature_valid": False,
                "errors": [],
            },
        }

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ManifestSigner")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_provisions_artifact_scoped_allowed_signers_for_validation(
        self, mock_pip_cls, mock_signer_cls, mock_validator_cls, tmp_path: Path
    ) -> None:
        signing_key = tmp_path / "release_key"
        public_key = signing_key.with_suffix(".pub")
        public_key.write_text("ssh-ed25519 AAAATEST release@example.com\n")
        allowed_signers = tmp_path / "allowed_signers"
        mock_pip_cls.return_value.build.return_value = _bundle_result(
            manifest_path=str(tmp_path / "MANIFEST.json"),
            sig_path=str(tmp_path / "MANIFEST.json.sig"),
            signature_valid=True,
        )
        mock_signer_cls.return_value.private_key_path = str(signing_key)
        mock_validator_cls.return_value.validate_release.return_value = _validation_result()

        build_and_validate_release(
            version="1.0.0",
            output_dir=str(tmp_path),
            allowed_signers_path=str(allowed_signers),
        )

        mock_signer_cls.return_value.make_allowed_signers.assert_called_once_with(
            "release-bundle", public_key.read_text().strip(), str(allowed_signers)
        )
        mock_validator_cls.assert_called_once_with(allowed_signers_path=str(allowed_signers))


class TestBuildAndValidateReleaseAllThree:
    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_default_image_ref_runtime_and_context_dir(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result()
        mock_container_cls.return_value.build_image.return_value = _build_result(
            image_ref="gl-agent:2.0.0", image_digest="sha256:abc123"
        )
        mock_validator_cls.return_value.validate_release.return_value = _validation_result()

        report = build_and_validate_release(
            version="2.0.0", output_dir="/out", build_container=True
        )

        mock_container_cls.return_value.build_image.assert_called_once_with(
            context_dir=".", image_ref="gl-agent:2.0.0", runtime="podman"
        )
        assert report["container"] == {
            "success": True,
            "image_ref": "gl-agent:2.0.0",
            "image_digest": "sha256:abc123",
        }

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_explicit_image_ref_runtime_and_context_dir(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result()
        mock_container_cls.return_value.build_image.return_value = _build_result()
        mock_validator_cls.return_value.validate_release.return_value = _validation_result()

        build_and_validate_release(
            version="3.0.0",
            output_dir="/out",
            build_container=True,
            context_dir="build/ctx",
            image_ref="registry.example.com/gl-agent:custom",
            container_runtime="docker",
        )

        mock_container_cls.return_value.build_image.assert_called_once_with(
            context_dir="build/ctx",
            image_ref="registry.example.com/gl-agent:custom",
            runtime="docker",
        )


class TestBuildAndValidateReleaseNonShortCircuitingFailures:
    """These pin down the REAL contract: a failure in an earlier stage does not
    stop later stages from running. Changing this to short-circuit would be a
    behavior change requiring these tests to be updated deliberately.
    """

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_bundle_failure_still_runs_container_and_validation(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result(
            success=False, wheel_path="", sdist_path="", manifest_path=""
        )
        mock_container_cls.return_value.build_image.return_value = _build_result(success=True)
        mock_validator_cls.return_value.validate_release.return_value = _validation_result(
            valid=False, errors=["MANIFEST.json not found in artifacts dir"]
        )

        report = build_and_validate_release(
            version="1.0.0", output_dir="/out", build_container=True
        )

        mock_container_cls.return_value.build_image.assert_called_once()
        mock_validator_cls.return_value.validate_release.assert_called_once()
        assert report["bundle"]["success"] is False
        assert report["container"]["success"] is True
        assert report["validation"]["valid"] is False

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_container_failure_still_validates(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result(success=True)
        mock_container_cls.return_value.build_image.return_value = _build_result(
            success=False, image_digest="", logs="podman not found on PATH"
        )
        mock_validator_cls.return_value.validate_release.return_value = _validation_result(
            valid=False, container_valid=False, errors=["Container image tags do not reference version 1.0.0"]
        )

        report = build_and_validate_release(
            version="1.0.0", output_dir="/out", build_container=True
        )

        mock_validator_cls.return_value.validate_release.assert_called_once_with(
            version="1.0.0", artifacts_dir="/out", require_container=True
        )
        assert report["container"]["success"] is False
        assert report["container"]["image_digest"] == ""
        assert report["validation"]["container_valid"] is False

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_container_validation_is_required_when_requested(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        """A requested container must be passed to the validator as required."""
        mock_pip_cls.return_value.build.return_value = _bundle_result(success=True)
        mock_container_cls.return_value.build_image.return_value = _build_result(
            success=False, image_digest="", logs="container build failed"
        )
        mock_validator_cls.return_value.validate_release.return_value = _validation_result(
            valid=False, container_valid=False, errors=["container artifact missing"]
        )

        build_and_validate_release(version="1.0.0", output_dir="/out", build_container=True)

        mock_validator_cls.return_value.validate_release.assert_called_once_with(
            version="1.0.0", artifacts_dir="/out", require_container=True
        )

    @patch("general_ludd.runtime.release_orchestrator.ReleaseArtifactValidator")
    @patch("general_ludd.runtime.release_orchestrator.ContainerBuilder")
    @patch("general_ludd.runtime.release_orchestrator.PipBundleBuilder")
    def test_validation_errors_reported_verbatim(
        self, mock_pip_cls, mock_container_cls, mock_validator_cls
    ) -> None:
        mock_pip_cls.return_value.build.return_value = _bundle_result(success=True)
        errors = [
            "Checksum mismatch for pkg-1.0.0-py3-none-any.whl",
            "Manifest version mismatch: expected 1.0.0",
        ]
        mock_validator_cls.return_value.validate_release.return_value = _validation_result(
            valid=False, pip_bundle_valid=False, manifest_valid=False, errors=errors
        )

        report = build_and_validate_release(version="1.0.0", output_dir="/out")

        assert report["validation"]["errors"] == errors
