"""End-to-end tests: Slurm billing — account/qos propagation + sub-hour time limits."""

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


class TestSlurmBillingE2E:
    def test_full_script_generation_with_account_and_qos(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="python train.py --epochs 100",
            account="billing-dept-42",
            qos="high-priority",
            time_limit="01:30:00",
        )
        assert "#!/bin/bash" in script
        assert "#SBATCH --account=billing-dept-42" in script
        assert "#SBATCH --qos=high-priority" in script
        assert "#SBATCH --time=01:30:00" in script
        assert "python train.py --epochs 100" in script

    def test_sub_hour_time_limit_in_full_script(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo quick job",
            account="testing",
            qos="express",
            time_limit="30:00",
        )
        assert "#SBATCH --time=30:00" in script
        assert "#SBATCH --account=testing" in script
        assert "#SBATCH --qos=express" in script

    def test_five_minute_time_limit_accepted(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo tiny job",
            time_limit="5:00",
        )
        assert "#SBATCH --time=5:00" in script

    def test_time_limit_hh_mm_ss_accepted(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo long job",
            time_limit="72:00:00",
        )
        assert "#SBATCH --time=72:00:00" in script

    def test_time_limit_int_defaults_to_hours(self):
        result = _resolve_time_limit(12, max_hours=24)
        assert result == "12:00:00"

    def test_account_validation_rejects_shell_injection(self):
        with pytest.raises(ValueError):
            _require_name("valid; rm -rf /", "account")

    def test_qos_validation_rejects_newline_injection(self):
        with pytest.raises(ValueError):
            _require_name("legit\n#SBATCH --gres=gpu:8", "qos")

    def test_slurm_job_config_billing_fields_roundtrip(self):
        cfg = SlurmJobConfig(
            account="billing-acct",
            qos="normal",
            time_limit_str="02:00:00",
            max_cost_usd=25.0,
            hourly_rate_usd=12.5,
        )
        assert cfg.account == "billing-acct"
        assert cfg.qos == "normal"
        assert cfg.time_limit_str == "02:00:00"
        assert cfg.max_cost_usd == 25.0
        assert cfg.hourly_rate_usd == 12.5

    def test_adapter_validates_all_billing_params_together(self):
        SlurmAdapter._validate_submit_params(
            job_name="test-job",
            partition=None,
            gpus=None,
            memory=None,
            time_limit="01:00:00",
            account="billing-dept",
            qos="express",
            output=None,
            extra_args=None,
        )

    def test_parse_total_minutes_all_formats(self):
        assert _parse_total_minutes("30") == 30
        assert _parse_total_minutes("45:00") == 45
        assert _parse_total_minutes("01:15:00") == 75
        assert _parse_total_minutes("0-12:00:00") == 720
        assert _parse_total_minutes("00:05:00") == 5

    def test_script_omits_account_and_qos_when_none(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(command="echo hi")
        assert "--account" not in script
        assert "--qos" not in script

    def test_script_includes_job_name_when_provided(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo job",
            job_name="my-named-job",
        )
        assert "#SBATCH --job-name=my-named-job" in script

    def test_script_includes_partition_when_provided(self):
        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo gpu-job",
            partition="gpu-a100",
        )
        assert "#SBATCH --partition=gpu-a100" in script
