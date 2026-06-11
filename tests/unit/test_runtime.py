"""Unit tests for runtime profiles."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.runtime.profile import (
    DataSourceMount,
    RuntimeProfile,
    RuntimeValidator,
)


class TestRuntimeProfile:
    def test_valid_native_uv_profile(self):
        profile = RuntimeProfile(
            runtime_profile_id="local-uv",
            mode="native_uv",
            project_root=".",
        )
        validator = RuntimeValidator()
        result = validator.validate_profile(profile)
        assert result["valid"] is True

    def test_invalid_mode(self):
        profile = RuntimeProfile(
            runtime_profile_id="bad",
            mode="invalid_mode",
        )
        validator = RuntimeValidator()
        result = validator.validate_profile(profile)
        assert result["valid"] is False

    def test_required_bind_mount_missing_host_path(self):
        profile = RuntimeProfile(
            runtime_profile_id="container-test",
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
        result = validator.validate_profile(profile)
        assert result["valid"] is False

    def test_relative_container_path_rejected(self):
        with pytest.raises(ValidationError, match="container_path must be absolute"):
            DataSourceMount(
                mount_id="data",
                source_type="bind",
                host_path="/data",
                container_path="relative/path",
                required=True,
            )
        assert True

    def test_valid_container_profile(self):
        profile = RuntimeProfile(
            runtime_profile_id="container-test",
            mode="container",
            mounts=[
                DataSourceMount(
                    mount_id="config",
                    source_type="bind",
                    host_path="./config",
                    container_path="/config",
                    access="ro",
                    required=True,
                ),
                DataSourceMount(
                    mount_id="artifacts",
                    source_type="named_volume",
                    volume_name="harness-artifacts",
                    container_path="/data/artifacts",
                    access="rw",
                    required=True,
                ),
            ],
        )
        validator = RuntimeValidator()
        result = validator.validate_profile(profile)
        assert result["valid"] is True
