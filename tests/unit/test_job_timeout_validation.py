"""Regression tests for JobSpec timeout validation (DoS guard)."""

import pytest
from pydantic import ValidationError

from general_ludd.schemas.job import JobSpec


def _base_kwargs(**overrides):
    defaults = {
        "job_id": "JOB-001",
        "playbook": "test_playbook",
        "queue": "core",
    }
    defaults.update(overrides)
    return defaults


class TestJobSpecTimeoutValidation:
    def test_negative_timeout_raises(self):
        with pytest.raises(ValidationError, match="timeout must be positive"):
            JobSpec(**_base_kwargs(timeout=-1))

    def test_zero_timeout_raises(self):
        with pytest.raises(ValidationError, match="timeout must be positive"):
            JobSpec(**_base_kwargs(timeout=0))

    def test_positive_timeout_accepted(self):
        spec = JobSpec(**_base_kwargs(timeout=30.0))
        assert spec.timeout == 30.0

    def test_none_timeout_accepted(self):
        spec = JobSpec(**_base_kwargs(timeout=None))
        assert spec.timeout is None

    def test_default_timeout_is_none(self):
        spec = JobSpec(**_base_kwargs())
        assert spec.timeout is None
