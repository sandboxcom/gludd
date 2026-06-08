"""Tests for the Slurm job adapter: sbatch submit, sacct status, scancel, graceful degradation."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobInfo,
    SlurmJobState,
    SlurmNotInstalledError,
)


class TestSlurmAdapterSubmit:
    def test_submit_returns_job_id_on_success(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 12345\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            job_id = adapter.submit(command="python3 train.py")
        assert job_id == "12345"

    def test_submit_builds_sbatch_script_with_directives(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 42\n"
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter.submit(
                command="python3 train.py",
                job_name="my-job",
                partition="gpu",
                cpus_per_task=4,
                gpus="1",
                memory="16G",
                time_limit="02:00:00",
                output="/tmp/slurm-%j.out",
            )

        args = captured["args"]
        assert args[0] == "sbatch"
        script = args[-1]
        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=my-job" in script
        assert "#SBATCH --partition=gpu" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --gres=gpu:1" in script
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --output=/tmp/slurm-%j.out" in script
        assert "python3 train.py" in script

    def test_submit_uses_stdin_pipe_for_script(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 1\n"
        captured = {}

        def fake_run(args, **kwargs):
            captured["kwargs"] = kwargs
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter.submit(command="echo hello")

        assert captured["kwargs"]["stdin"] == subprocess.PIPE
        assert captured["kwargs"]["input"] is not None

    def test_submit_raises_on_nonzero_returncode(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "sbatch: error: Batch job submission failed\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result), \
             pytest.raises(RuntimeError, match="sbatch failed"):
            adapter.submit(command="python3 train.py")

    def test_submit_raises_slurm_not_installed(self):
        adapter = SlurmAdapter()
        with patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=FileNotFoundError("No such file 'sbatch'"),
        ), pytest.raises(SlurmNotInstalledError):
            adapter.submit(command="python3 train.py")

    def test_submit_minimal_command_no_options(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 99\n"
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            job_id = adapter.submit(command="/usr/bin/myapp")

        assert job_id == "99"
        script = captured["args"][-1]
        assert "#!/bin/bash" in script
        assert "/usr/bin/myapp" in script

    def test_submit_extra_sbatch_args(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 55\n"
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter.submit(
                command="python3 run.py",
                extra_args=["--account=myproj", "--mail-type=END"],
            )

        args = captured["args"]
        assert "--account=myproj" in args
        assert "--mail-type=END" in args


class TestSlurmAdapterStatus:
    def test_query_returns_job_info_completed(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|COMPLETED|0\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert isinstance(info, SlurmJobInfo)
        assert info.job_id == "12345"
        assert info.state == SlurmJobState.COMPLETED
        assert info.exit_code == 0

    def test_query_returns_job_info_running(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|RUNNING|\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert info.state == SlurmJobState.RUNNING
        assert info.exit_code is None

    def test_query_returns_job_info_failed(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|FAILED|1\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert info.state == SlurmJobState.FAILED
        assert info.exit_code == 1

    def test_query_returns_pending(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|PENDING|\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert info.state == SlurmJobState.PENDING

    def test_query_uses_sacct_format(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|COMPLETED|0\n"
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter.status("12345")

        args = captured["args"]
        assert args[0] == "sacct"
        format_args = [a for a in args if a.startswith("--format")]
        assert len(format_args) == 1
        assert "JobID" in format_args[0]
        assert "State" in format_args[0]
        assert "ExitCode" in format_args[0]
        assert "--parsable2" in args
        assert "--noheader" in args
        assert "--jobs" in args
        assert "12345" in args

    def test_query_raises_slurm_not_installed(self):
        adapter = SlurmAdapter()
        with patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=FileNotFoundError,
        ), pytest.raises(SlurmNotInstalledError):
            adapter.status("12345")

    def test_query_unknown_state(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|SOMETHING_WEIRD|0\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert info.state == SlurmJobState.UNKNOWN

    def test_query_no_output_returns_unknown(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            info = adapter.status("12345")
        assert info.state == SlurmJobState.UNKNOWN
        assert info.job_id == "12345"


class TestSlurmAdapterCancel:
    def test_cancel_calls_scancel(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter.cancel("12345")

        assert captured["args"] == ["scancel", "12345"]

    def test_cancel_raises_slurm_not_installed(self):
        adapter = SlurmAdapter()
        with patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=FileNotFoundError,
        ), pytest.raises(SlurmNotInstalledError):
            adapter.cancel("12345")

    def test_cancel_raises_on_nonzero_returncode(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "scancel: error: Invalid job id\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result), \
             pytest.raises(RuntimeError, match="scancel failed"):
            adapter.cancel("99999")


class TestSlurmAdapterAvailable:
    def test_available_returns_true_when_installed(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter.available() is True

    def test_available_returns_false_when_not_installed(self):
        adapter = SlurmAdapter()
        with patch(
            "general_ludd.infra.slurm.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert adapter.available() is False


class TestSlurmJobState:
    def test_all_expected_states_exist(self):
        expected = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "UNKNOWN"}
        actual = {s.value for s in SlurmJobState}
        assert expected == actual

    def test_state_from_string(self):
        assert SlurmJobState.from_string("RUNNING") == SlurmJobState.RUNNING
        assert SlurmJobState.from_string("COMPLETED") == SlurmJobState.COMPLETED
        assert SlurmJobState.from_string("CANCELLED") == SlurmJobState.CANCELLED
        assert SlurmJobState.from_string("TIMEOUT") == SlurmJobState.TIMEOUT
        assert SlurmJobState.from_string("NODE_FAIL") == SlurmJobState.NODE_FAIL
        assert SlurmJobState.from_string("BOGUS") == SlurmJobState.UNKNOWN


class TestSlurmJobInfo:
    def test_job_info_defaults(self):
        info = SlurmJobInfo(job_id="12345", state=SlurmJobState.RUNNING)
        assert info.job_id == "12345"
        assert info.state == SlurmJobState.RUNNING
        assert info.exit_code is None

    def test_job_info_completed_with_exit_code(self):
        info = SlurmJobInfo(job_id="99", state=SlurmJobState.FAILED, exit_code=137)
        assert info.exit_code == 137
