"""Structural tests for project_runner/runner.py — target project check runner."""

import tempfile
from pathlib import Path

from general_ludd.project_runner.profile import ProjectProfile, ProjectProfileError
from general_ludd.project_runner.runner import (
    _BASE_ENV_KEYS,
    _DEFAULT_TIMEOUT_S,
    _SECRET_NAME_RE,
    _TAIL_CHARS,
    CheckResult,
    ProjectCommandRunner,
    _BoundedReader,
    _build_env,
)


class TestCheckResult:
    def test_default_construction(self):
        cr = CheckResult(
            name="test-check",
            exit_code=0,
            passed=True,
            duration_s=1.5,
        )
        assert cr.name == "test-check"
        assert cr.passed is True
        assert cr.timed_out is False
        assert cr.oom_killed is False

    def test_summary_pass(self):
        cr = CheckResult(name="lint", exit_code=0, passed=True, duration_s=2.0)
        assert "PASS" in cr.summary()
        assert "lint" in cr.summary()

    def test_summary_fail(self):
        cr = CheckResult(name="lint", exit_code=1, passed=False, duration_s=2.0)
        assert "FAIL" in cr.summary()

    def test_summary_timeout(self):
        cr = CheckResult(
            name="lint", exit_code=None, passed=False, duration_s=900, timed_out=True,
        )
        assert "TIMED OUT" in cr.summary()

    def test_summary_oom(self):
        cr = CheckResult(
            name="lint", exit_code=-9, passed=False, duration_s=5.0, oom_killed=True,
        )
        assert "OOM-KILLED" in cr.summary()

    def test_summary_error(self):
        cr = CheckResult(
            name="lint", exit_code=None, passed=False, duration_s=0.1,
            error="not found",
        )
        assert "ERROR" in cr.summary()

    def test_summary_anomalous_duration(self):
        cr = CheckResult(
            name="lint", exit_code=0, passed=True, duration_s=60.0,
            anomalous_duration=True, baseline_s=10.0,
        )
        assert "SLOW" in cr.summary()

    def test_findings_default_empty(self):
        cr = CheckResult(name="x", exit_code=0, passed=True, duration_s=1.0)
        assert cr.findings == []


class TestBuildEnv:
    def test_returns_dict_with_path(self):
        env = _build_env([])
        assert isinstance(env, dict)
        assert "PATH" in env

    def test_passthrough_forwarded(self):
        import os
        os.environ["TEST_PASSTHROUGH_FOO"] = "bar"
        try:
            env = _build_env(["TEST_PASSTHROUGH_FOO"])
            assert env["TEST_PASSTHROUGH_FOO"] == "bar"
        finally:
            del os.environ["TEST_PASSTHROUGH_FOO"]

    def test_secret_names_refused(self):
        # KEY/TOKEN/SECRET pattern names should be refused
        env = _build_env(["MY_API_KEY", "AWS_SECRET_TOKEN"])
        assert "MY_API_KEY" not in env
        assert "AWS_SECRET_TOKEN" not in env


class TestSecretNameRe:
    def test_matches_key(self):
        assert _SECRET_NAME_RE.search("API_KEY")

    def test_matches_token(self):
        assert _SECRET_NAME_RE.search("AUTH_TOKEN")

    def test_matches_secret(self):
        assert _SECRET_NAME_RE.search("AWS_SECRET_ACCESS_KEY")

    def test_does_not_match_normal(self):
        assert not _SECRET_NAME_RE.search("MY_CONFIG_SETTING")

    def test_case_insensitive(self):
        assert _SECRET_NAME_RE.search("api_key")


class TestBoundedReader:
    def test_construct(self):
        import io
        stream = io.StringIO("hello world")
        reader = _BoundedReader(stream, cap=100)
        assert reader is not None

    def test_text_property_exists(self):
        import io
        stream = io.StringIO("hello")
        reader = _BoundedReader(stream, cap=100)
        assert hasattr(reader, "text")


class TestProjectCommandRunner:
    def test_construct_with_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = ProjectProfile()
            runner = ProjectCommandRunner(workspace=tmp, profile=profile)
            assert runner.workspace == Path(tmp).resolve()

    def test_construct_nonexistent_dir_raises(self):
        profile = ProjectProfile()
        try:
            ProjectCommandRunner(workspace="/nonexistent/path/12345", profile=profile)
            raise AssertionError("should have raised")
        except ProjectProfileError:
            pass

    def test_constants_defined(self):
        assert isinstance(_TAIL_CHARS, int)
        assert isinstance(_DEFAULT_TIMEOUT_S, int)
        assert isinstance(_BASE_ENV_KEYS, tuple)
        assert "PATH" in _BASE_ENV_KEYS
