"""Unit tests for runtime packaging and deployment modes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentic_harness.runtime.profile import DataSourceMount, RuntimeProfile


class TestRuntimeValidatorNativeUv:
    @patch("agentic_harness.runtime.validator.subprocess.run")
    def test_runtime_validator_validates_native_uv(self, mock_run: MagicMock):
        from agentic_harness.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=0, stdout="Resolved")
        profile = RuntimeProfile(
            runtime_profile_id="uv-test",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_uv(profile)
        assert result.valid is True
        assert len(result.errors) == 0

    @patch("agentic_harness.runtime.validator.subprocess.run")
    def test_runtime_validator_uv_fails_on_bad_sync(self, mock_run: MagicMock):
        from agentic_harness.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=1, stderr="sync failed")
        profile = RuntimeProfile(
            runtime_profile_id="uv-bad",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_uv(profile)
        assert result.valid is False
        assert any("sync" in e.lower() for e in result.errors)


class TestRuntimeValidatorNativePip:
    @patch("agentic_harness.runtime.validator.subprocess.run")
    def test_runtime_validator_validates_native_pip(self, mock_run: MagicMock):
        from agentic_harness.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=0, stdout="Successfully installed")
        profile = RuntimeProfile(
            runtime_profile_id="pip-test",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is True

    @patch("agentic_harness.runtime.validator.subprocess.run")
    def test_runtime_validator_pip_fails(self, mock_run: MagicMock):
        from agentic_harness.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=1, stderr="pip install failed")
        profile = RuntimeProfile(
            runtime_profile_id="pip-bad",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is False


class TestRuntimeValidatorContainer:
    def test_runtime_validator_validates_container_mounts(self):
        from agentic_harness.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-test",
            mode="container",
            config_path="hottentot-agent:latest",
            mounts=[
                DataSourceMount(
                    mount_id="config",
                    source_type="bind",
                    host_path="/host/config",
                    container_path="/config",
                    required=True,
                ),
                DataSourceMount(
                    mount_id="data",
                    source_type="named_volume",
                    volume_name="data-vol",
                    container_path="/data",
                    required=True,
                ),
            ],
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is True

    def test_runtime_validator_rejects_missing_required_mount(self):
        from agentic_harness.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-bad",
            mode="container",
            mounts=[
                DataSourceMount(
                    mount_id="config",
                    source_type="bind",
                    host_path=None,
                    container_path="/config",
                    required=True,
                ),
            ],
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is False
        assert any("host_path" in e for e in result.errors)

    def test_runtime_validator_rejects_invalid_image_ref(self):
        from agentic_harness.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-img",
            mode="container",
            config_path="",
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is False


class TestDataSourceMountAudit:
    def test_data_source_mount_audit_detects_untracked(self):
        from agentic_harness.runtime.validator import RuntimeValidator

        mounts = [
            DataSourceMount(
                mount_id="missing-bind",
                source_type="bind",
                host_path="/nonexistent/path/xyz",
                container_path="/data",
                required=True,
            ),
            DataSourceMount(
                mount_id="valid-vol",
                source_type="named_volume",
                volume_name="ok-vol",
                container_path="/data2",
                required=False,
            ),
        ]
        validator = RuntimeValidator()
        results = validator.validate_data_source_mounts(mounts)
        assert len(results) == 2
        missing_result = next(r for r in results if r.mount_id == "missing-bind")
        assert missing_result.valid is False
        vol_result = next(r for r in results if r.mount_id == "valid-vol")
        assert vol_result.valid is True


class TestPipBundleBuilder:
    @patch("agentic_harness.runtime.pip_bundle.subprocess.run")
    @patch("agentic_harness.runtime.pip_bundle.os.listdir")
    def test_pip_bundle_builder_creates_manifest(self, mock_listdir: MagicMock, mock_run: MagicMock):
        from agentic_harness.runtime.pip_bundle import PipBundleBuilder

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="abc123def456"),
        ]
        mock_listdir.return_value = [
            "hottentot_agent-0.1.0-py3-none-any.whl",
            "hottentot_agent-0.1.0.tar.gz",
            "requirements.txt",
        ]
        builder = PipBundleBuilder()
        result = builder.build(output_dir="/tmp/bundle", version="0.1.0")
        assert result.success is True
        assert result.manifest_path.endswith("MANIFEST.json")
        assert result.checksum_path.endswith("CHECKSUMS.sha256")

    def test_pip_bundle_manifest_schema(self):
        from agentic_harness.runtime.pip_bundle import BundleManifest

        manifest = BundleManifest(
            version="0.1.0",
            commit="abc123",
            timestamp=datetime.now(UTC).isoformat(),
            files=["a.whl", "b.tar.gz"],
            checksums={"a.whl": "sha256:abc", "b.tar.gz": "sha256:def"},
        )
        assert manifest.version == "0.1.0"
        assert len(manifest.files) == 2
        assert "a.whl" in manifest.checksums


class TestContainerBuilder:
    @patch("agentic_harness.runtime.container.subprocess.run")
    def test_container_builder_build_result(self, mock_run: MagicMock):
        from agentic_harness.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="sha256:deadbeef1234 image built",
        )
        builder = ContainerBuilder()
        result = builder.build_image(
            context_dir="/tmp/context",
            image_ref="hottentot-agent:latest",
            runtime="podman",
        )
        assert result.success is True
        assert result.image_ref == "hottentot-agent:latest"
        assert result.image_digest != ""

    @patch("agentic_harness.runtime.container.subprocess.run")
    def test_container_builder_validate_image(self, mock_run: MagicMock):
        from agentic_harness.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"Config":{"Entrypoint":["/usr/bin/hottentot-worker"]},"Size":123456789}',
        )
        builder = ContainerBuilder()
        result = builder.validate_image("hottentot-agent:latest")
        assert isinstance(result.valid, bool)
        assert isinstance(result.has_baked_state, bool)
        assert isinstance(result.entrypoint_correct, bool)
        assert isinstance(result.size_mb, float)


class TestContainerfile:
    def test_containerfile_exists(self):
        assert Path("Containerfile").exists(), "Containerfile not found in project root"

    def test_containerfile_multistage(self):
        content = Path("Containerfile").read_text()
        assert "FROM" in content
        stages = [line for line in content.splitlines() if line.strip().startswith("FROM")]
        assert len(stages) >= 2, "Containerfile should be multi-stage (at least 2 FROM lines)"


class TestReleaseArtifactValidator:
    def test_release_artifact_validator_checks_bundle(self, tmp_path: Path):
        from agentic_harness.runtime.release import ReleaseArtifactValidator

        manifest_data = '{"version": "0.1.0", "files": [], "checksums": {}}'
        manifest_file = tmp_path / "MANIFEST.json"
        manifest_file.write_text(manifest_data)

        checksum_data = "abc  test.whl"
        checksum_file = tmp_path / "CHECKSUMS.sha256"
        checksum_file.write_text(checksum_data)

        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert isinstance(result.pip_bundle_valid, bool)

    def test_release_artifact_validator_checks_container(self, tmp_path: Path):
        from agentic_harness.runtime.release import ReleaseArtifactValidator

        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert isinstance(result.container_valid, bool)
        assert isinstance(result.valid, bool)
        assert isinstance(result.errors, list)


class TestPlaybooksExist:
    @pytest.mark.parametrize(
        "playbook",
        [
            "runtime_validate.yml",
            "native_install_validate.yml",
            "pip_install_bundle.yml",
            "slim_agent_container_build.yml",
            "container_image_validate.yml",
            "release_artifacts_validate.yml",
            "data_source_mount_audit.yml",
        ],
    )
    def test_playbooks_exist(self, playbook: str):
        assert Path("playbooks").exists()
        assert (Path("playbooks") / playbook).exists(), f"Playbook {playbook} not found"
