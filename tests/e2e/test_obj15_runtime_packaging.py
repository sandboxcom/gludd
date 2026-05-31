"""E2E: Runtime packaging — pip bundle, container build, release validation (sprint objective 15).

Covers PipBundleBuilder, ContainerBuilder, ReleaseArtifactValidator,
RuntimeProfile, RuntimeValidator, Containerfile validity, and playbook stubs.
"""

from __future__ import annotations

import json
import os

from agentic_harness.runtime import (
    BuildResult,
    BundleManifest,
    BundleResult,
    ContainerBuilder,
    DataSourceMount,
    ImageValidationResult,
    PipBundleBuilder,
    ReleaseArtifactValidator,
    ReleaseValidationResult,
    RuntimeProfile,
    RuntimeValidator,
)


class TestPipBundleBuilderImport:
    def test_pip_bundle_builder_importable(self):
        assert PipBundleBuilder is not None

    def test_pip_bundle_builder_instantiation(self):
        builder = PipBundleBuilder()
        assert builder is not None

    def test_bundle_manifest_model(self):
        manifest = BundleManifest(
            version="0.1.0",
            commit="abc123",
            timestamp="2026-01-01T00:00:00Z",
            files=["test.whl"],
            checksums={"test.whl": "sha256:deadbeef"},
        )
        assert manifest.version == "0.1.0"
        assert manifest.commit == "abc123"
        data = manifest.model_dump()
        assert data["version"] == "0.1.0"

    def test_bundle_result_dataclass(self):
        result = BundleResult(
            bundle_path="/tmp/dist",
            wheel_path="/tmp/dist/pkg.whl",
            sdist_path="/tmp/dist/pkg.tar.gz",
            manifest_path="/tmp/dist/MANIFEST.json",
            checksum_path="/tmp/dist/CHECKSUMS.sha256",
            success=True,
        )
        assert result.success is True
        assert result.wheel_path.endswith(".whl")


class TestContainerBuilderImport:
    def test_container_builder_importable(self):
        assert ContainerBuilder is not None

    def test_container_builder_instantiation(self):
        builder = ContainerBuilder()
        assert builder is not None

    def test_build_result_dataclass(self):
        result = BuildResult(
            image_ref="agentic-harness:latest",
            image_digest="sha256:abc123",
            success=True,
            logs="built",
        )
        assert result.success is True
        assert result.image_ref == "agentic-harness:latest"

    def test_image_validation_result_dataclass(self):
        result = ImageValidationResult(
            valid=True,
            has_baked_state=False,
            entrypoint_correct=True,
            size_mb=150.0,
        )
        assert result.valid is True
        assert result.has_baked_state is False
        assert result.size_mb == 150.0


class TestReleaseValidation:
    def test_release_artifact_validator_importable(self):
        assert ReleaseArtifactValidator is not None

    def test_release_artifact_validator_instantiation(self):
        validator = ReleaseArtifactValidator()
        assert validator is not None

    def test_release_validation_result_dataclass(self):
        result = ReleaseValidationResult(
            valid=True,
            pip_bundle_valid=True,
            container_valid=True,
            manifest_valid=True,
        )
        assert result.valid is True
        assert result.errors == []

    def test_validate_release_empty_dir(self, tmp_path):
        validator = ReleaseArtifactValidator()
        result = validator.validate_release("0.1.0", str(tmp_path))
        assert result.valid is False
        assert result.pip_bundle_valid is False

    def test_validate_release_with_manifest_and_checksums(self, tmp_path):
        import hashlib

        wheel_content = b"fake-wheel-bytes"
        wheel_name = "agentic_harness-0.1.0-py3-none-any.whl"
        wheel_path = tmp_path / wheel_name
        wheel_path.write_bytes(wheel_content)
        checksum = f"sha256:{hashlib.sha256(wheel_content).hexdigest()}"

        manifest = {
            "version": "0.1.0",
            "commit": "abc123",
            "timestamp": "2026-01-01T00:00:00Z",
            "files": [wheel_name],
            "checksums": {wheel_name: checksum},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        (tmp_path / "CHECKSUMS.sha256").write_text(f"{checksum}  {wheel_name}\n")

        validator = ReleaseArtifactValidator()
        result = validator.validate_release("0.1.0", str(tmp_path))
        assert result.pip_bundle_valid is True
        assert result.manifest_valid is True
        assert result.valid is True

    def test_validate_release_version_mismatch(self, tmp_path):
        manifest = {
            "version": "0.2.0",
            "commit": "abc123",
            "timestamp": "2026-01-01T00:00:00Z",
            "files": [],
            "checksums": {},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        (tmp_path / "CHECKSUMS.sha256").write_text("")

        validator = ReleaseArtifactValidator()
        result = validator.validate_release("0.1.0", str(tmp_path))
        assert result.manifest_valid is False
        assert any("version mismatch" in e for e in result.errors)

    def test_validate_release_checksum_mismatch(self, tmp_path):
        wheel_name = "test.whl"
        (tmp_path / wheel_name).write_bytes(b"content")
        manifest = {
            "version": "0.1.0",
            "commit": "abc123",
            "timestamp": "2026-01-01T00:00:00Z",
            "files": [wheel_name],
            "checksums": {wheel_name: "sha256:baddigest"},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        (tmp_path / "CHECKSUMS.sha256").write_text("sha256:baddigest  test.whl\n")

        validator = ReleaseArtifactValidator()
        result = validator.validate_release("0.1.0", str(tmp_path))
        assert result.pip_bundle_valid is False
        assert any("mismatch" in e for e in result.errors)

    def test_validate_release_with_container_tags(self, tmp_path):
        tags = {"images": ["agentic-harness:0.1.0"]}
        (tmp_path / "container-image-tags.json").write_text(json.dumps(tags))
        manifest = {
            "version": "0.1.0",
            "commit": "abc",
            "timestamp": "2026-01-01T00:00:00Z",
            "files": [],
            "checksums": {},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        (tmp_path / "CHECKSUMS.sha256").write_text("")

        validator = ReleaseArtifactValidator()
        result = validator.validate_release("0.1.0", str(tmp_path))
        assert result.container_valid is True


class TestRuntimeProfile:
    def test_runtime_profile_creation(self):
        profile = RuntimeProfile(runtime_profile_id="test-profile")
        assert profile.runtime_profile_id == "test-profile"
        assert profile.mode == "native_uv"
        assert profile.enabled is True

    def test_runtime_profile_with_mounts(self):
        mount = DataSourceMount(
            mount_id="config",
            purpose="config",
            required=True,
            source_type="bind",
            host_path="/etc/harness",
            container_path="/config",
            access="ro",
        )
        profile = RuntimeProfile(
            runtime_profile_id="container-profile",
            mode="container",
            mounts=[mount],
        )
        assert profile.mode == "container"
        assert len(profile.mounts) == 1
        assert profile.mounts[0].host_path == "/etc/harness"

    def test_data_source_mount_defaults(self):
        mount = DataSourceMount(mount_id="test-mount")
        assert mount.source_type == "bind"
        assert mount.access == "ro"
        assert mount.required is True
        assert mount.secret_safe is False
        assert mount.model_visible is False


class TestRuntimeValidatorImport:
    def test_runtime_validator_importable(self):
        assert RuntimeValidator is not None

    def test_validate_profile_valid_native_uv(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="uv-1", mode="native_uv")
        result = v.validate_profile(profile)
        assert result["valid"] is True

    def test_validate_profile_invalid_mode(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="bad", mode="invalid_mode")
        result = v.validate_profile(profile)
        assert result["valid"] is False

    def test_validate_profile_required_bind_mount_missing_host_path(self):
        v = RuntimeValidator()
        mount = DataSourceMount(
            mount_id="data",
            required=True,
            source_type="bind",
            host_path=None,
            container_path="/data",
        )
        profile = RuntimeProfile(
            runtime_profile_id="con-1",
            mode="container",
            mounts=[mount],
        )
        result = v.validate_profile(profile)
        assert result["valid"] is False

    def test_validate_profile_relative_container_path(self):
        v = RuntimeValidator()
        mount = DataSourceMount(
            mount_id="bad-path",
            container_path="relative/path",
        )
        profile = RuntimeProfile(
            runtime_profile_id="con-2",
            mode="container",
            mounts=[mount],
        )
        result = v.validate_profile(profile)
        assert result["valid"] is False


class TestRuntimeValidatorModes:
    def test_validate_container_requires_config_path(self):
        from agentic_harness.runtime.validator import RuntimeValidator as RV

        v = RV()
        profile = RuntimeProfile(
            runtime_profile_id="con-3",
            mode="container",
            config_path=None,
        )
        result = v.validate_container(profile)
        assert result.valid is False
        assert any("image reference" in e for e in result.errors)

    def test_validate_native_uv_wrong_mode(self):
        from agentic_harness.runtime.validator import RuntimeValidator as RV

        v = RV()
        profile = RuntimeProfile(
            runtime_profile_id="pip-1",
            mode="native_pip",
        )
        result = v.validate_native_uv(profile)
        assert result.valid is False

    def test_validate_native_pip_wrong_mode(self):
        from agentic_harness.runtime.validator import RuntimeValidator as RV

        v = RV()
        profile = RuntimeProfile(
            runtime_profile_id="uv-2",
            mode="native_uv",
        )
        result = v.validate_native_pip(profile)
        assert result.valid is False

    def test_validate_data_source_mounts_bind_missing_source(self, tmp_path):
        from agentic_harness.runtime.validator import RuntimeValidator as RV

        v = RV()
        mounts = [
            DataSourceMount(
                mount_id="missing-src",
                required=True,
                source_type="bind",
                host_path="/nonexistent/path/xyz",
                container_path="/data",
            ),
        ]
        results = v.validate_data_source_mounts(mounts)
        assert len(results) == 1
        assert results[0].valid is False
        assert results[0].mount_id == "missing-src"

    def test_validate_data_source_mounts_named_volume_missing_name(self):
        from agentic_harness.runtime.validator import RuntimeValidator as RV

        v = RV()
        mounts = [
            DataSourceMount(
                mount_id="vol-1",
                source_type="named_volume",
                volume_name=None,
                container_path="/data",
            ),
        ]
        results = v.validate_data_source_mounts(mounts)
        assert results[0].valid is False


class TestContainerfile:
    def test_containerfile_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        assert os.path.isfile(path)

    def test_containerfile_has_from_directive(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "FROM" in content

    def test_containerfile_multi_stage_build(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        from_count = content.upper().count("FROM")
        assert from_count >= 2

    def test_containerfile_has_entrypoint(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "ENTRYPOINT" in content

    def test_containerfile_has_healthcheck(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "HEALTHCHECK" in content

    def test_containerfile_non_root_user(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "USER" in content

    def test_containerfile_builds_from_python_slim(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "python:" in content
        assert "slim" in content

    def test_containerfile_copies_wheel(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert ".whl" in content

    def test_containerfile_pip_install_wheel(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "Containerfile")
        with open(path) as f:
            content = f.read()
        assert "pip install" in content


class TestRuntimePackagingPlaybookStubs:
    def test_pip_install_bundle_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "pip_install_bundle.yml")
        assert os.path.isfile(path)

    def test_slim_agent_container_build_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "slim_agent_container_build.yml")
        assert os.path.isfile(path)

    def test_release_artifacts_validate_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "release_artifacts_validate.yml")
        assert os.path.isfile(path)

    def test_container_image_validate_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "container_image_validate.yml")
        assert os.path.isfile(path)

    def test_runtime_validate_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "runtime_validate.yml")
        assert os.path.isfile(path)

    def test_native_install_validate_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "native_install_validate.yml")
        assert os.path.isfile(path)

    def test_all_runtime_playbooks_valid_yaml(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        playbooks = [
            "pip_install_bundle.yml",
            "slim_agent_container_build.yml",
            "release_artifacts_validate.yml",
            "container_image_validate.yml",
            "runtime_validate.yml",
            "native_install_validate.yml",
        ]
        for pb in playbooks:
            path = os.path.join(repo_root, "playbooks", pb)
            with open(path) as f:
                data = yaml.safe_load(f)
            assert isinstance(data, list), f"{pb} is not a valid playbook list"
            assert len(data) >= 1, f"{pb} has no plays"
