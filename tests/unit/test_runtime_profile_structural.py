"""Structural tests for runtime/profile.py — DataSourceMount, RuntimeProfile, RuntimeValidator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.runtime.profile import (
    DataSourceMount,
    RuntimeProfile,
    RuntimeValidator,
)


class TestDataSourceMount:
    def test_defaults(self):
        mount = DataSourceMount(mount_id="vol-1")
        assert mount.mount_id == "vol-1"
        assert mount.purpose == "config"
        assert mount.required is True
        assert mount.source_type == "bind"
        assert mount.host_path is None
        assert mount.volume_name is None
        assert mount.container_path == "/data"
        assert mount.native_path is None
        assert mount.access == "ro"
        assert mount.create_if_missing is False
        assert mount.secret_safe is False
        assert mount.model_visible is False

    def test_empty_mount_id_raises(self):
        with pytest.raises(ValidationError):
            DataSourceMount(mount_id="")

    def test_whitespace_mount_id_raises(self):
        with pytest.raises(ValidationError):
            DataSourceMount(mount_id="   ")

    def test_relative_container_path_raises(self):
        with pytest.raises(ValidationError):
            DataSourceMount(mount_id="v1", container_path="relative/path")

    def test_absolute_container_path_ok(self):
        mount = DataSourceMount(mount_id="v1", container_path="/abs/path")
        assert mount.container_path == "/abs/path"

    def test_model_dump_includes_all_fields(self):
        mount = DataSourceMount(
            mount_id="m1",
            purpose="data",
            required=False,
            source_type="volume",
            volume_name="v1",
            container_path="/mnt",
            access="rw",
            create_if_missing=True,
            secret_safe=True,
            model_visible=True,
        )
        d = mount.model_dump()
        assert d["mount_id"] == "m1"
        assert d["purpose"] == "data"
        assert d["required"] is False
        assert d["source_type"] == "volume"
        assert d["volume_name"] == "v1"
        assert d["access"] == "rw"
        assert d["create_if_missing"] is True
        assert d["secret_safe"] is True
        assert d["model_visible"] is True


class TestRuntimeProfile:
    def test_defaults(self):
        rp = RuntimeProfile(runtime_profile_id="rp-1")
        assert rp.runtime_profile_id == "rp-1"
        assert rp.mode == "native_uv"
        assert rp.enabled is True
        assert rp.project_root == "."
        assert rp.config_path is None
        assert rp.python_version_constraint is None
        assert rp.entrypoint is None
        assert rp.healthcheck_url == "http://localhost:8000/healthz"
        assert rp.required_services == ["postgres"]
        assert rp.mounts == []

    def test_empty_runtime_profile_id_raises(self):
        with pytest.raises(ValidationError):
            RuntimeProfile(runtime_profile_id="")

    def test_with_mounts(self):
        rp = RuntimeProfile(
            runtime_profile_id="rp-2",
            mounts=[
                DataSourceMount(mount_id="v1", container_path="/data"),
                DataSourceMount(mount_id="v2", container_path="/secrets", access="rw"),
            ],
        )
        assert len(rp.mounts) == 2
        assert rp.mounts[0].mount_id == "v1"
        assert rp.mounts[1].mount_id == "v2"

    def test_model_dump(self):
        rp = RuntimeProfile(
            runtime_profile_id="rp-3",
            mode="container",
            entrypoint="python -m app",
            required_services=["redis", "postgres"],
        )
        d = rp.model_dump()
        assert d["runtime_profile_id"] == "rp-3"
        assert d["mode"] == "container"
        assert d["entrypoint"] == "python -m app"
        assert d["required_services"] == ["redis", "postgres"]


class TestRuntimeValidator:
    def test_validator_exists(self):
        v = RuntimeValidator()
        assert hasattr(v, "validate_profile")

    def test_valid_profile_passes(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="rp-ok")
        result = v.validate_profile(profile)
        assert result["valid"] is True
        assert result["issues"] == []

    def test_invalid_mode_fails(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="rp-bad", mode="docker_compose")
        result = v.validate_profile(profile)
        assert result["valid"] is False
        assert any("mode" in issue.lower() for issue in result["issues"])

    def test_required_bind_mount_missing_host_path(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(
            runtime_profile_id="rp-mount",
            mounts=[DataSourceMount(mount_id="bad-mount", required=True, source_type="bind")],
        )
        result = v.validate_profile(profile)
        assert result["valid"] is False
        assert any("host_path" in issue.lower() for issue in result["issues"])

    def test_relative_container_path_in_mount(self):
        v = RuntimeValidator()
        mount = DataSourceMount(mount_id="m1")
        # Force an invalid container_path by constructing after validation
        object.__setattr__(mount, "container_path", "rel/path")
        profile = RuntimeProfile(runtime_profile_id="rp-rel", mounts=[mount])
        result = v.validate_profile(profile)
        assert result["valid"] is False

    def test_native_pip_mode_is_valid(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="rp-pip", mode="native_pip")
        result = v.validate_profile(profile)
        assert result["valid"] is True

    def test_container_mode_is_valid(self):
        v = RuntimeValidator()
        profile = RuntimeProfile(runtime_profile_id="rp-ctr", mode="container")
        result = v.validate_profile(profile)
        assert result["valid"] is True
