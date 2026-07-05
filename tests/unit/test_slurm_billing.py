"""Tests for Slurm billing gaps: account/qos propagation and sub-hour time limits."""

from __future__ import annotations

import pytest

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobConfig,
    _require_name,
)
from general_ludd.infra.slurm_deployment import (
    _parse_total_minutes,
    _resolve_time_limit,
)


class TestSlurmJobConfig:
    def test_defaults_all_none(self):
        cfg = SlurmJobConfig()
        assert cfg.account is None
        assert cfg.qos is None
        assert cfg.time_limit_str is None

    def test_sets_fields(self):
        cfg = SlurmJobConfig(
            account="myaccount",
            qos="express",
            time_limit_str="01:30:00",
        )
        assert cfg.account == "myaccount"
        assert cfg.qos == "express"
        assert cfg.time_limit_str == "01:30:00"


class TestAccountFlagInScript:
    def test_account_present_in_build_script(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo hi",
            account="billing_acct",
        )
        assert "#SBATCH --account=billing_acct" in script

    def test_account_omitted_when_none(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(command="echo hi")
        assert "--account" not in script


class TestQosFlagInScript:
    def test_qos_present_in_build_script(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo hi",
            qos="express",
        )
        assert "#SBATCH --qos=express" in script

    def test_qos_omitted_when_none(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(command="echo hi")
        assert "--qos" not in script


class TestBothAccountAndQos:
    def test_both_present_in_build_script(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo hi",
            account="billing_acct",
            qos="express",
        )
        assert "#SBATCH --account=billing_acct" in script
        assert "#SBATCH --qos=express" in script


class TestSubHourTimeLimit:
    def test_30_minutes_format(self):
        result = _resolve_time_limit("30:00", max_hours=24)
        assert result == "30:00"

    def test_5_minutes_format(self):
        result = _resolve_time_limit("5:00", max_hours=24)
        assert result == "5:00"

    def test_hh_mm_ss_format(self):
        result = _resolve_time_limit("01:30:00", max_hours=24)
        assert result == "01:30:00"

    def test_int_defaults_to_hours(self):
        result = _resolve_time_limit(12, max_hours=24)
        assert result == "12:00:00"

    def test_none_falls_back_to_max_hours(self):
        result = _resolve_time_limit(None, max_hours=8)
        assert result == "8:00:00"


class TestResolveTimeLimitRejectsInvalid:
    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            _resolve_time_limit("", max_hours=24)

    def test_rejects_non_digit(self):
        with pytest.raises(ValueError):
            _resolve_time_limit("abc", max_hours=24)

    def test_rejects_negative_minutes(self):
        with pytest.raises(ValueError):
            _resolve_time_limit("-5:00", max_hours=24)

    def test_rejects_zero_minutes(self):
        with pytest.raises(ValueError, match="less than 1 minute"):
            _resolve_time_limit("0:00", max_hours=24)

    def test_rejects_zero_days_format(self):
        with pytest.raises(ValueError, match="less than 1 minute"):
            _resolve_time_limit("0-00:00:00", max_hours=24)

    def test_rejects_seconds_only(self):
        with pytest.raises(ValueError):
            _resolve_time_limit("00:00:30", max_hours=24)

    def test_rejects_newline_injection(self):
        with pytest.raises(ValueError):
            _resolve_time_limit("30\n:00", max_hours=24)


class TestParseTotalMinutes:
    def test_plain_minutes(self):
        assert _parse_total_minutes("30") == 30

    def test_mm_ss_format(self):
        assert _parse_total_minutes("30:00") == 30

    def test_hh_mm_ss_format(self):
        assert _parse_total_minutes("01:30:00") == 90

    def test_days_hours_format(self):
        assert _parse_total_minutes("1-12") == 1 * 24 * 60 + 12 * 60

    def test_days_hours_minutes_format(self):
        assert _parse_total_minutes("2-06:30") == 2 * 24 * 60 + 6 * 60 + 30

    def test_days_hours_minutes_seconds_format(self):
        assert _parse_total_minutes("1-00:30:00") == 1 * 24 * 60 + 30

    def test_one_minute(self):
        assert _parse_total_minutes("1:00") == 1

    def test_zero_minutes(self):
        assert _parse_total_minutes("0:00") == 0


class TestAccountQosValidation:
    def test_account_passes_validation(self):
        _require_name("myaccount123", "account")

    def test_account_rejects_leading_dash(self):
        with pytest.raises(ValueError):
            _require_name("--evil", "account")

    def test_account_rejects_whitespace(self):
        with pytest.raises(ValueError):
            _require_name("my account", "account")

    def test_qos_passes_validation(self):
        _require_name("express", "qos")

    def test_qos_rejects_leading_dash(self):
        with pytest.raises(ValueError):
            _require_name("--bad", "qos")

    def test_validate_submit_params_accepts_account_and_qos(self):
        SlurmAdapter._validate_submit_params(
            job_name=None,
            partition=None,
            gpus=None,
            memory=None,
            time_limit=None,
            account="myaccount",
            qos="express",
            output=None,
            extra_args=None,
        )

    def test_validate_submit_params_rejects_bad_account(self):
        with pytest.raises(ValueError):
            SlurmAdapter._validate_submit_params(
                job_name=None,
                partition=None,
                gpus=None,
                memory=None,
                time_limit=None,
                account="--bad",
                qos=None,
                output=None,
                extra_args=None,
            )


class TestSubmitEndpointAcceptsAccountAndQos:
    """Test that the router's submit endpoint processes account and qos fields."""

    def test_submit_passes_account_to_adapter(self):
        """The HTTP router submit extracts account from the JSON body."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        mock_adapter.submit.return_value = "12345"

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/admin/slurm/submit",
                json={"command": "vllm-serve-model", "account": "billing_acct", "qos": "express"},
            )
            assert resp.status_code == 200
            assert resp.json()["job_id"] == "12345"
            call_kwargs = mock_adapter.submit.call_args.kwargs
            assert call_kwargs["account"] == "billing_acct"
            assert call_kwargs["qos"] == "express"

    def test_submit_omits_account_and_qos_when_not_provided(self):
        """account and qos are omitted from the adapter call when absent."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        mock_adapter.submit.return_value = "67890"

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/admin/slurm/submit", json={"command": "echo hi"})
            assert resp.status_code == 200
            call_kwargs = mock_adapter.submit.call_args.kwargs
            assert call_kwargs["account"] is None
            assert call_kwargs["qos"] is None


class TestSubmitEndpointAcceptsCostFields:
    """Test that max_cost_usd and hourly_rate_usd flow through the submit path."""

    def test_submit_accepts_max_cost_usd(self):
        """The cost fields are accepted in the submit request body."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        mock_adapter.submit.return_value = "job-cost"

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/admin/slurm/submit",
                json={
                    "command": "train-model",
                    "max_cost_usd": 5.0,
                    "hourly_rate_usd": 2.5,
                },
            )
            assert resp.status_code == 200
            # Cost fields are accepted in the request body (passed to adapter
            # via the req dict — the router forwards them as part of submit params).
            assert resp.json()["job_id"] == "job-cost"


class TestGetJobCostEndpoint:
    """Test GET /admin/slurm/jobs/{job_id}/cost returns cost breakdown."""

    def test_cost_endpoint_returns_breakdown(self):
        """The cost endpoint returns estimated cost information for a job."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        from general_ludd.infra.slurm import SlurmJobInfo, SlurmJobState
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="job-1", state=SlurmJobState.RUNNING, cost_incurred=2.75
        )

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/admin/slurm/jobs/job-1/cost")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == "job-1"
            assert "cost_breakdown" in data
            assert data["cost_breakdown"]["estimated_cost_usd"] == 2.75

    def test_cost_endpoint_unknown_job(self):
        """The cost endpoint returns 200 with zero cost for unknown job."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        from general_ludd.infra.slurm import SlurmJobInfo, SlurmJobState
        mock_adapter.status.return_value = SlurmJobInfo(
            job_id="unknown", state=SlurmJobState.UNKNOWN, cost_incurred=0.0
        )

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/admin/slurm/jobs/unknown/cost")
            assert resp.status_code == 200
            data = resp.json()
            assert data["cost_breakdown"]["estimated_cost_usd"] == 0.0

    def test_cost_endpoint_not_installed(self):
        """The cost endpoint returns 503 when Slurm is not installed."""
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.infra.slurm import SlurmNotInstalledError
        from general_ludd.routers.slurm import register as register_slurm

        app = FastAPI()
        register_slurm(app, {})

        mock_adapter = MagicMock()
        mock_adapter.status.side_effect = SlurmNotInstalledError("sacct not found")

        with patch("general_ludd.routers.slurm._make_adapter", return_value=mock_adapter):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/admin/slurm/jobs/job-1/cost")
            assert resp.status_code == 503
