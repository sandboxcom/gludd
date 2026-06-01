"""Unit tests for runtime packaging and deployment modes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runtime.profile import DataSourceMount, RuntimeProfile


class TestRuntimeValidatorNativeUv:
    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_validates_native_uv(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

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

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_uv_fails_on_bad_sync(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

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

    def test_runtime_validator_uv_wrong_mode(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="pip-mode",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_uv(profile)
        assert result.valid is False
        assert any("native_uv" in e for e in result.errors)

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_uv_not_found(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.side_effect = FileNotFoundError()
        profile = RuntimeProfile(
            runtime_profile_id="uv-test",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_uv(profile)
        assert result.valid is False
        assert any("not found" in e for e in result.errors)

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_uv_timeout(self, mock_run: MagicMock):
        import subprocess

        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=60)
        profile = RuntimeProfile(
            runtime_profile_id="uv-test",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_uv(profile)
        assert result.valid is False
        assert any("timed out" in e for e in result.errors)


class TestRuntimeValidatorNativePip:
    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_validates_native_pip(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=0, stdout="Successfully installed")
        profile = RuntimeProfile(
            runtime_profile_id="pip-test",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is True

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_pip_fails(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.return_value = MagicMock(returncode=1, stderr="pip install failed")
        profile = RuntimeProfile(
            runtime_profile_id="pip-bad",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is False

    def test_runtime_validator_pip_wrong_mode(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="uv-mode",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is False
        assert any("native_pip" in e for e in result.errors)

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_pip_not_found(self, mock_run: MagicMock):
        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.side_effect = FileNotFoundError()
        profile = RuntimeProfile(
            runtime_profile_id="pip-test",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is False
        assert any("not found" in e for e in result.errors)

    @patch("general_ludd.runtime.validator.subprocess.run")
    def test_runtime_validator_pip_timeout(self, mock_run: MagicMock):
        import subprocess

        from general_ludd.runtime.validator import RuntimeValidator

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=120)
        profile = RuntimeProfile(
            runtime_profile_id="pip-test",
            mode="native_pip",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_native_pip(profile)
        assert result.valid is False
        assert any("timed out" in e for e in result.errors)


class TestRuntimeValidatorContainer:
    def test_runtime_validator_validates_container_mounts(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-test",
            mode="container",
            config_path="gl-agent:latest",
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
        from general_ludd.runtime.validator import RuntimeValidator

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
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-img",
            mode="container",
            config_path="",
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is False

    def test_runtime_validator_container_wrong_mode(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="uv-mode",
            mode="native_uv",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is False
        assert any("container" in e for e in result.errors)

    def test_runtime_validator_container_relative_path(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="container-rel",
            mode="container",
            config_path="img:latest",
            mounts=[
                DataSourceMount(
                    mount_id="data",
                    source_type="bind",
                    host_path="/host/data",
                    container_path="relative/path",
                    required=True,
                ),
            ],
        )
        validator = RuntimeValidator()
        result = validator.validate_container(profile)
        assert result.valid is False
        assert any("absolute" in e.lower() for e in result.errors)

    def test_validate_profile_invalid_mode(self):
        from general_ludd.runtime.validator import RuntimeValidator

        profile = RuntimeProfile(
            runtime_profile_id="bad-mode",
            mode="invalid_mode",
            project_root="/tmp/project",
        )
        validator = RuntimeValidator()
        result = validator.validate_profile(profile)
        assert result["valid"] is False
        assert any("Invalid mode" in e for e in result["issues"])


class TestDataSourceMountAudit:
    def test_data_source_mount_audit_detects_untracked(self):
        from general_ludd.runtime.validator import RuntimeValidator

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

    def test_data_source_mount_relative_container_path(self):
        from general_ludd.runtime.validator import RuntimeValidator

        mounts = [
            DataSourceMount(
                mount_id="rel-path",
                source_type="bind",
                host_path="/host/data",
                container_path="relative/path",
                required=True,
            ),
        ]
        validator = RuntimeValidator()
        results = validator.validate_data_source_mounts(mounts)
        assert results[0].valid is False
        assert any("absolute" in e.lower() for e in results[0].errors)

    def test_data_source_mount_missing_volume_name(self):
        from general_ludd.runtime.validator import RuntimeValidator

        mounts = [
            DataSourceMount(
                mount_id="no-vol",
                source_type="named_volume",
                volume_name="",
                container_path="/data",
                required=True,
            ),
        ]
        validator = RuntimeValidator()
        results = validator.validate_data_source_mounts(mounts)
        assert results[0].valid is False
        assert any("volume_name" in e for e in results[0].errors)

    def test_data_source_mount_required_no_host_path(self):
        from general_ludd.runtime.validator import RuntimeValidator

        mounts = [
            DataSourceMount(
                mount_id="no-host",
                source_type="bind",
                host_path=None,
                container_path="/data",
                required=True,
            ),
        ]
        validator = RuntimeValidator()
        results = validator.validate_data_source_mounts(mounts)
        assert results[0].valid is False
        assert any("host_path" in e for e in results[0].errors)


class TestPipBundleBuilder:
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    @patch("general_ludd.runtime.pip_bundle.os.listdir")
    def test_pip_bundle_builder_creates_manifest(self, mock_listdir: MagicMock, mock_run: MagicMock):
        from general_ludd.runtime.pip_bundle import PipBundleBuilder

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="abc123def456"),
        ]
        mock_listdir.return_value = [
            "general_ludd_agent-0.1.0-py3-none-any.whl",
            "general_ludd_agent-0.1.0.tar.gz",
            "requirements.txt",
        ]
        builder = PipBundleBuilder()
        result = builder.build(output_dir="/tmp/bundle", version="0.1.0")
        assert result.success is True
        assert result.manifest_path.endswith("MANIFEST.json")
        assert result.checksum_path.endswith("CHECKSUMS.sha256")

    def test_pip_bundle_manifest_schema(self):
        from general_ludd.runtime.pip_bundle import BundleManifest

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
    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_builder_build_result(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="sha256:deadbeef1234 image built",
        )
        builder = ContainerBuilder()
        result = builder.build_image(
            context_dir="/tmp/context",
            image_ref="gl-agent:latest",
            runtime="podman",
        )
        assert result.success is True
        assert result.image_ref == "gl-agent:latest"
        assert result.image_digest != ""

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_builder_validate_image(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"Config":{"Entrypoint":["/usr/bin/gludd-worker"]},"Size":123456789}',
        )
        builder = ContainerBuilder()
        result = builder.validate_image("gl-agent:latest")
        assert isinstance(result.valid, bool)
        assert isinstance(result.has_baked_state, bool)
        assert isinstance(result.entrypoint_correct, bool)
        assert isinstance(result.size_mb, float)

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_builder_build_failure(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(returncode=1, stderr="build error")
        builder = ContainerBuilder()
        result = builder.build_image("/ctx", "img:bad")
        assert result.success is False
        assert "build error" in result.logs

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_builder_build_not_found(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.side_effect = FileNotFoundError()
        builder = ContainerBuilder()
        result = builder.build_image("/ctx", "img:latest", runtime="podman")
        assert result.success is False
        assert "not found" in result.logs

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_builder_build_timeout(self, mock_run: MagicMock):
        import subprocess

        from general_ludd.runtime.container import ContainerBuilder

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="podman", timeout=600)
        builder = ContainerBuilder()
        result = builder.build_image("/ctx", "img:latest")
        assert result.success is False
        assert "timed out" in result.logs

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_validate_image_inspect_failure(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        builder = ContainerBuilder()
        result = builder.validate_image("missing:latest")
        assert result.valid is False

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_validate_image_bad_json(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        builder = ContainerBuilder()
        result = builder.validate_image("img:latest")
        assert result.valid is False

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_validate_image_detects_baked_secrets(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        inspect_data = (
            '{"Config":{"Entrypoint":["gludd"],"Env":["API_KEY=secret123"]},"Size":1048576}'
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=inspect_data)
        builder = ContainerBuilder()
        result = builder.validate_image("img:latest")
        assert result.has_baked_state is True

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_validate_image_size_string(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        inspect_data = '{"Config":{"Entrypoint":["gludd"]},"Size":"5242880"}'
        mock_run.return_value = MagicMock(returncode=0, stdout=inspect_data)
        builder = ContainerBuilder()
        result = builder.validate_image("img:latest")
        assert result.valid is True
        assert result.size_mb > 0.0

    @patch("general_ludd.runtime.container.subprocess.run")
    def test_container_validate_image_not_found_runtime(self, mock_run: MagicMock):
        from general_ludd.runtime.container import ContainerBuilder

        mock_run.side_effect = FileNotFoundError()
        builder = ContainerBuilder()
        result = builder.validate_image("img:latest")
        assert result.valid is False


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
        from general_ludd.runtime.release import ReleaseArtifactValidator

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
        from general_ludd.runtime.release import ReleaseArtifactValidator

        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert isinstance(result.container_valid, bool)
        assert isinstance(result.valid, bool)
        assert isinstance(result.errors, list)

    def test_release_missing_manifest(self, tmp_path: Path):
        from general_ludd.runtime.release import ReleaseArtifactValidator

        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert result.pip_bundle_valid is False
        assert any("MANIFEST" in e for e in result.errors)

    def test_release_checksum_mismatch(self, tmp_path: Path):
        from general_ludd.runtime.release import ReleaseArtifactValidator

        (tmp_path / "MANIFEST.json").write_text(
            '{"version": "0.1.0", "checksums": {"app.whl": "sha256:badhash"}}'
        )
        (tmp_path / "CHECKSUMS.sha256").write_text("placeholder")
        (tmp_path / "app.whl").write_bytes(b"content")
        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert result.pip_bundle_valid is False
        assert any("mismatch" in e.lower() for e in result.errors)

    def test_release_version_mismatch(self, tmp_path: Path):
        from general_ludd.runtime.release import ReleaseArtifactValidator

        (tmp_path / "MANIFEST.json").write_text('{"version": "0.2.0", "checksums": {}}')
        (tmp_path / "CHECKSUMS.sha256").write_text("placeholder")
        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert result.manifest_valid is False

    def test_release_container_tags_version_missing(self, tmp_path: Path):
        from general_ludd.runtime.release import ReleaseArtifactValidator

        (tmp_path / "MANIFEST.json").write_text('{"version": "0.1.0", "checksums": {}}')
        (tmp_path / "CHECKSUMS.sha256").write_text("placeholder")
        (tmp_path / "container-image-tags.json").write_text('{"tags": ["latest"]}')
        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert result.container_valid is False

    def test_release_container_tags_bad_json(self, tmp_path: Path):
        from general_ludd.runtime.release import ReleaseArtifactValidator

        (tmp_path / "MANIFEST.json").write_text('{"version": "0.1.0", "checksums": {}}')
        (tmp_path / "CHECKSUMS.sha256").write_text("placeholder")
        (tmp_path / "container-image-tags.json").write_text("not json")
        validator = ReleaseArtifactValidator()
        result = validator.validate_release(version="0.1.0", artifacts_dir=str(tmp_path))
        assert result.container_valid is False


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
