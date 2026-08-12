"""Deep tests for slurm.py: validation edge cases, REST paths, internal helpers, JobConfig, and monitor gaps."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.infra.slurm import (
    _JOB_ID_RE,
    _NAME_RE,
    _TERMINAL_STATES,
    _TIME_RE,
    SlurmAdapter,
    SlurmConnectionError,
    SlurmJobConfig,
    SlurmJobInfo,
    SlurmJobMonitor,
    SlurmJobState,
    SlurmNotInstalledError,
    _parse_elapsed,
    _require_extra_arg,
    _require_job_id,
    _require_name,
    _require_output,
    _require_time,
)

# ========================================================================== #
# _parse_elapsed — time-format parsing edge cases
# ========================================================================== #


class TestParseElapsedDeep:
    def test_d_hh_not_handled_by_parser(self):
        # D-HH (no minutes): the docstring claims it is supported but the
        # implementation only handles D-HH:MM and D-HH:MM:SS. The time_part
        # "HH" has no colons → len(time_parts)==1 → returns None.
        assert _parse_elapsed("3-12") is None

    def test_d_hh_mm_is_interpreted_as_days_minutes_seconds(self):
        # D-HH:MM with 2 colon parts → minutes:seconds, not hours:minutes.
        # 1 day + 02:30 (2m30s) = 86400 + 150 = 86550.0
        assert _parse_elapsed("1-02:30") == 86550.0

    def test_HH_MM_SS(self):
        assert _parse_elapsed("100:00:00") == 100 * 3600

    def test_malformed_non_numeric_day(self):
        assert _parse_elapsed("abc-12:00:00") is None

    def test_malformed_four_parts(self):
        assert _parse_elapsed("1:2:3:4") is None

    def test_leading_trailing_whitespace(self):
        assert _parse_elapsed("  01:30:45  ") == 5445.0

    def test_negative_is_none(self):
        assert _parse_elapsed("-01:00:00") is None

    def test_lowercase_unlimited(self):
        assert _parse_elapsed("unlimited") is None

    def test_mixed_case_unlimited(self):
        assert _parse_elapsed("UnLiMiTeD") is None


# ========================================================================== #
# validation functions — type checks, edge formats
# ========================================================================== #


class TestRequireJobIdDeep:
    def test_rejects_int(self):
        with pytest.raises(ValueError):
            _require_job_id(42)  # type: ignore[arg-type]

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            _require_job_id(None)  # type: ignore[arg-type]

    def test_accepts_array_id(self):
        assert _require_job_id("123_4") == "123_4"

    def test_rejects_step_id_letters(self):
        with pytest.raises(ValueError) as exc_info:
            _require_job_id("123.batch")
        assert "invalid Slurm job id" in str(exc_info.value)

    def test_rejects_array_step_letters(self):
        with pytest.raises(ValueError) as exc_info:
            _require_job_id("123_4.batch")
        assert "invalid Slurm job id" in str(exc_info.value)

    def test_accepts_plus_suffix(self):
        assert _require_job_id("123+") == "123+"

    def test_rejects_leading_letter(self):
        with pytest.raises(ValueError):
            _require_job_id("a123")

    def test_rejects_whitespace_inside(self):
        with pytest.raises(ValueError):
            _require_job_id("123 456")


class TestRequireNameDeep:
    def test_rejects_none(self):
        with pytest.raises(ValueError):
            _require_name(None, "test")  # type: ignore[arg-type]

    def test_rejects_int(self):
        with pytest.raises(ValueError):
            _require_name(123, "test")  # type: ignore[arg-type]

    def test_accepts_colon_plus(self):
        assert _require_name("foo:bar+baz", "test") == "foo:bar+baz"

    def test_accepts_dot(self):
        assert _require_name("gpu.v100", "partition") == "gpu.v100"

    def test_accepts_dash_inside(self):
        assert _require_name("my-job-1", "job_name") == "my-job-1"

    def test_rejects_leading_dash(self):
        with pytest.raises(ValueError):
            _require_name("-myjob", "job_name")

    def test_rejects_newline_inside(self):
        with pytest.raises(ValueError):
            _require_name("good\nbad", "job_name")

    def test_rejects_whitespace(self):
        with pytest.raises(ValueError):
            _require_name("my job", "job_name")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError):
            _require_name("foo|bar", "gpUs")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError):
            _require_name("foo;bar", "partition")


class TestRequireTimeDeep:
    def test_accepts_plain_minutes(self):
        assert _require_time("30") == "30"

    def test_accepts_minutes_seconds(self):
        assert _require_time("30:00") == "30:00"

    def test_accepts_days_hours(self):
        assert _require_time("3-06") == "3-06"

    def test_accepts_days_hours_minutes(self):
        assert _require_time("3-06:30") == "3-06:30"

    def test_rejects_leading_dash(self):
        with pytest.raises(ValueError):
            _require_time("-01:00:00")

    def test_rejects_embedded_newline(self):
        with pytest.raises(ValueError):
            _require_time("01:00\n#SBATCH --partition=hijack")

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            _require_time(None)  # type: ignore[arg-type]

    def test_rejects_letters(self):
        with pytest.raises(ValueError):
            _require_time("abc")

    def test_rejects_double_day(self):
        with pytest.raises(ValueError):
            _require_time("1-2-03:00")


class TestRequireExtraArgDeep:
    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            _require_extra_arg("")

    def test_rejects_newline(self):
        with pytest.raises(ValueError):
            _require_extra_arg("--ok\n--evil")

    def test_rejects_cr(self):
        with pytest.raises(ValueError):
            _require_extra_arg("--ok\r--evil")

    def test_rejects_nul(self):
        with pytest.raises(ValueError):
            _require_extra_arg("--ok\x00--evil")

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            _require_extra_arg(None)  # type: ignore[arg-type]

    def test_accepts_valid_flag(self):
        assert _require_extra_arg("--account=myproj") == "--account=myproj"

    def test_accepts_valid_long_arg(self):
        assert _require_extra_arg("--mail-type=END") == "--mail-type=END"


class TestRequireOutputDeep:
    def test_rejects_none(self):
        with pytest.raises(ValueError):
            _require_output(None)  # type: ignore[arg-type]

    def test_accepts_spaces_in_path(self):
        assert _require_output("/tmp/my job.out") == "/tmp/my job.out"

    def test_accepts_percent_patterns(self):
        assert _require_output("/var/log/slurm_%j_%a.out") == "/var/log/slurm_%j_%a.out"

    def test_rejects_int(self):
        with pytest.raises(ValueError):
            _require_output(123)  # type: ignore[arg-type]


# ========================================================================== #
# regex compile-time assertions
# ========================================================================== #


class TestRegexPatterns:
    @pytest.mark.parametrize("valid_id", ["123", "1", "999999", "123_4", "123_4+5"])
    def test_job_id_re_accepts_valid(self, valid_id):
        assert _JOB_ID_RE.match(valid_id)

    @pytest.mark.parametrize("invalid_id", ["", "abc", "-123", "123 ", "123\n4"])
    def test_job_id_re_rejects_invalid(self, invalid_id):
        assert _JOB_ID_RE.match(invalid_id) is None

    @pytest.mark.parametrize("valid_name", ["gpu", "my_job", "a-b", "v100:200", "foo.bar", "hpc+sc"])
    def test_name_re_accepts_valid(self, valid_name):
        assert _NAME_RE.match(valid_name)

    @pytest.mark.parametrize("invalid_name", ["-gpu", "gpu ", "my\njob", "a|b"])
    def test_name_re_rejects_invalid(self, invalid_name):
        assert _NAME_RE.match(invalid_name) is None

    @pytest.mark.parametrize("valid_time", ["30", "30:00", "01:30:45", "2-06:15:30", "3-06:15", "3-06"])
    def test_time_re_accepts_valid(self, valid_time):
        assert _TIME_RE.match(valid_time)

    @pytest.mark.parametrize("invalid_time", ["", "-01:00", "abc", "1:2:3:4"])
    def test_time_re_rejects_invalid(self, invalid_time):
        assert _TIME_RE.match(invalid_time) is None

    # _TIME_RE uses re.match (anchored at start only via ^) and `$`
    # matches before a trailing newline, so "01:00\n" matches. The
    # newline injection guard is in _require_time, not the regex.
    def test_time_re_matches_before_trailing_newline(self):
        assert _TIME_RE.match("01:00\n") is not None


# ========================================================================== #
# SlurmJobConfig
# ========================================================================== #


class TestSlurmJobConfig:
    def test_all_fields_default_to_none(self):
        cfg = SlurmJobConfig()
        assert cfg.account is None
        assert cfg.qos is None
        assert cfg.time_limit_str is None
        assert cfg.max_cost_usd is None
        assert cfg.idle_timeout_minutes is None
        assert cfg.hourly_rate_usd is None

    def test_explicit_values(self):
        cfg = SlurmJobConfig(
            account="myaccount",
            qos="high",
            time_limit_str="01:00:00",
            max_cost_usd=5.0,
            idle_timeout_minutes=10.0,
            hourly_rate_usd=2.5,
        )
        assert cfg.account == "myaccount"
        assert cfg.qos == "high"
        assert cfg.time_limit_str == "01:00:00"
        assert cfg.max_cost_usd == 5.0
        assert cfg.idle_timeout_minutes == 10.0
        assert cfg.hourly_rate_usd == 2.5


# ========================================================================== #
# SlurmJobInfo — default fields
# ========================================================================== #


class TestSlurmJobInfoDeep:
    def test_cost_default(self):
        info = SlurmJobInfo("1", SlurmJobState.RUNNING)
        assert info.cost_incurred == 0.0

    def test_original_job_id_default(self):
        info = SlurmJobInfo("1", SlurmJobState.RUNNING)
        assert info.original_job_id is None

    def test_resubmit_count_default(self):
        info = SlurmJobInfo("1", SlurmJobState.RUNNING)
        assert info.resubmit_count == 0

    def test_all_fields_set(self):
        info = SlurmJobInfo(
            job_id="42",
            state=SlurmJobState.COMPLETED,
            exit_code=0,
            cost_incurred=12.5,
            original_job_id="41",
            resubmit_count=3,
        )
        assert info.job_id == "42"
        assert info.state == SlurmJobState.COMPLETED
        assert info.exit_code == 0
        assert info.cost_incurred == 12.5
        assert info.original_job_id == "41"
        assert info.resubmit_count == 3


# ========================================================================== #
# SlurmJobState.from_string — whitespace / casing edge cases
# ========================================================================== #


class TestSlurmJobStateDeep:
    def test_whitespace_stripping(self):
        assert SlurmJobState.from_string("  RUNNING  ") == SlurmJobState.RUNNING

    def test_lowercase_input(self):
        assert SlurmJobState.from_string("running") == SlurmJobState.RUNNING

    def test_mixed_case_input(self):
        assert SlurmJobState.from_string("RuNnInG") == SlurmJobState.RUNNING

    def test_preempted(self):
        assert SlurmJobState.from_string("PREEMPTED") == SlurmJobState.PREEMPTED

    def test_node_fail(self):
        assert SlurmJobState.from_string("NODE_FAIL") == SlurmJobState.NODE_FAIL

    def test_timeout(self):
        assert SlurmJobState.from_string("TIMEOUT") == SlurmJobState.TIMEOUT


# ========================================================================== #
# _TERMINAL_STATES — completeness
# ========================================================================== #


class TestTerminalStates:
    def test_contains_all_expected(self):
        expected = {
            SlurmJobState.COMPLETED,
            SlurmJobState.FAILED,
            SlurmJobState.CANCELLED,
            SlurmJobState.TIMEOUT,
            SlurmJobState.NODE_FAIL,
        }
        assert expected == _TERMINAL_STATES

    def test_does_not_contain_running(self):
        assert SlurmJobState.RUNNING not in _TERMINAL_STATES

    def test_does_not_contain_pending(self):
        assert SlurmJobState.PENDING not in _TERMINAL_STATES

    def test_is_frozenset(self):
        assert isinstance(_TERMINAL_STATES, frozenset)


# ========================================================================== #
# SlurmAdapter._is_remote  /  _headers  /  _api_base
# ========================================================================== #


class TestAdapterProperties:
    def test_is_remote_false_by_default(self):
        assert SlurmAdapter()._is_remote is False

    def test_is_remote_true_with_url(self):
        assert SlurmAdapter(api_url="http://slurm:6820")._is_remote is True

    def test_headers_no_token(self):
        headers = SlurmAdapter()._headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["X-SLURM-USER-NAME"] == "slurm"
        assert "X-SLURM-USER-TOKEN" not in headers

    def test_headers_with_token(self):
        headers = SlurmAdapter(auth_token="mytoken")._headers()
        assert headers["X-SLURM-USER-TOKEN"] == "mytoken"

    def test_api_base_strips_trailing_slash(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820/")
        base = adapter._api_base()
        assert base == "http://slurm:6820/slurm/v0.0.40"

    def test_api_base_no_url_returns_slash_slurm_version(self):
        adapter = SlurmAdapter()
        base = adapter._api_base()
        assert base == "/slurm/v0.0.40"


# ========================================================================== #
# SlurmAdapter._request — HTTP dispatch
# ========================================================================== #


class TestAdapterRequest:
    def test_get_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp) as get:
            resp = adapter._request("Get", "http://slurm:6820/slurm/v0.0.40/jobs", headers=adapter._headers())
            assert resp.status_code == 200
            get.assert_called_once()

    def test_post_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        with patch("general_ludd.infra.slurm.httpx.post", return_value=mock_resp) as post:
            adapter._request("POST", "http://url", json={})
            post.assert_called_once()

    def test_delete_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        with patch("general_ludd.infra.slurm.httpx.delete", return_value=mock_resp) as delete:
            adapter._request("DELETE", "http://url")
            delete.assert_called_once()

    def test_unsupported_method_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with pytest.raises(ValueError, match="unsupported method"):
            adapter._request("PATCH", "http://url")

    def test_connect_error_wraps_to_slurm_connection_error(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with (
            patch("general_ludd.infra.slurm.httpx.get", side_effect=httpx.ConnectError("refused")),
            pytest.raises(SlurmConnectionError, match="Slurm REST API unreachable"),
        ):
            adapter._request("GET", "http://url")

    def test_timeout_wraps_to_slurm_connection_error(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with (
            patch("general_ludd.infra.slurm.httpx.get", side_effect=httpx.TimeoutException("too slow")),
            pytest.raises(SlurmConnectionError, match="Slurm REST API unreachable"),
        ):
            adapter._request("GET", "http://url")

    def test_network_error_wraps_to_slurm_connection_error(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with (
            patch("general_ludd.infra.slurm.httpx.get", side_effect=httpx.NetworkError("offline")),
            pytest.raises(SlurmConnectionError, match="Slurm REST API unreachable"),
        ):
            adapter._request("GET", "http://url")


# ========================================================================== #
# _parse_job_id — stdout parsing edge cases
# ========================================================================== #


class TestParseJobId:
    def test_standard_output(self):
        assert SlurmAdapter._parse_job_id("Submitted batch job 12345\n") == "12345"

    def test_extra_whitespace(self):
        assert SlurmAdapter._parse_job_id("  Submitted batch job  42  \n") == "42"

    def test_multi_line_returns_first_match(self):
        # _parse_job_id iterates lines and returns the FIRST match
        stdout = "Submitted batch job 10\nSubmitted batch job 99\n"
        assert SlurmAdapter._parse_job_id(stdout) == "10"

    def test_raises_on_no_match(self):
        with pytest.raises(RuntimeError, match="Could not parse job ID"):
            SlurmAdapter._parse_job_id("some garbage output\n")


# ========================================================================== #
# _parse_sacct_line — line parsing edge cases
# ========================================================================== #


class TestParseSacctLine:
    def test_three_fields(self):
        info = SlurmAdapter._parse_sacct_line("42", "42|RUNNING|0")
        assert info.job_id == "42"
        assert info.state == SlurmJobState.RUNNING
        assert info.exit_code == 0

    def test_two_fields_no_exit_code(self):
        info = SlurmAdapter._parse_sacct_line("42", "42|RUNNING")
        assert info.state == SlurmJobState.RUNNING
        assert info.exit_code is None

    def test_one_field_only(self):
        info = SlurmAdapter._parse_sacct_line("42", "42")
        assert info.state == SlurmJobState.UNKNOWN
        assert info.exit_code is None

    def test_exit_code_empty_string(self):
        info = SlurmAdapter._parse_sacct_line("42", "42|COMPLETED|")
        assert info.exit_code is None

    def test_exit_code_whitespace_only(self):
        info = SlurmAdapter._parse_sacct_line("42", "42|COMPLETED|   ")
        assert info.exit_code is None


# ========================================================================== #
# _local_elapsed_seconds — sacct Elapsed parsing
# ========================================================================== #


class TestLocalElapsedSeconds:
    @pytest.fixture
    def adapter(self):
        return SlurmAdapter()

    def test_returns_seconds(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|01:30:00\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            elapsed = adapter._local_elapsed_seconds("12345")
        assert elapsed == 5400.0

    def test_multi_line_picks_correct_job(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345.batch|00:05:00\n12345|01:30:00\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            elapsed = adapter._local_elapsed_seconds("12345")
        assert elapsed == 5400.0

    def test_job_not_in_output_returns_none(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "99999|01:00:00\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_file_not_found_returns_none(self, adapter):
        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_timeout_returns_none(self, adapter):
        with patch(
            "general_ludd.infra.slurm.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["sacct"], timeout=60)
        ):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_nonzero_returncode_returns_none(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_empty_stdout_returns_none(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_partial_pipe_line_returns_none(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter._local_elapsed_seconds("12345") is None

    def test_uses_correct_sacct_args(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["run_kwargs"] = kwargs
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter._local_elapsed_seconds("12345")

        args = captured["args"]
        assert args[0] == "sacct"
        assert "--format=JobID,Elapsed" in args
        assert "--parsable2" in args
        assert "--noheader" in args
        assert "--jobs=12345" in args
        assert captured["run_kwargs"]["timeout"] == 60


# ========================================================================== #
# elapsed_seconds — routing (local vs remote)
# ========================================================================== #


class TestElapsedSecondsRouting:
    def test_remote_returns_none(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        assert adapter.elapsed_seconds("12345") is None

    def test_local_delegates_to_local_method(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|00:10:00\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter.elapsed_seconds("12345") == 600.0


# ========================================================================== #
# _local_list_jobs — squeue edge cases
# ========================================================================== #


class TestLocalListJobs:
    @pytest.fixture
    def adapter(self):
        return SlurmAdapter()

    def test_returns_parsed_jobs(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|RUNNING\n12346|PENDING\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            jobs = adapter._local_list_jobs()
        assert len(jobs) == 2
        assert jobs[0].job_id == "12345"
        assert jobs[0].state == SlurmJobState.RUNNING
        assert jobs[1].job_id == "12346"
        assert jobs[1].state == SlurmJobState.PENDING

    def test_empty_stdout(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            assert adapter._local_list_jobs() == []

    def test_file_not_found_reports_not_installed(self, adapter):
        with (
            patch("general_ludd.infra.slurm.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(SlurmNotInstalledError, match="squeue not found"),
        ):
            adapter._local_list_jobs()

    def test_timeout_reports_controller_unavailable(self, adapter):
        with (
            patch(
                "general_ludd.infra.slurm.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["squeue"], timeout=30),
            ),
            pytest.raises(SlurmConnectionError, match="timed out"),
        ):
            adapter._local_list_jobs()

    def test_nonzero_returncode_reports_failure(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "scheduler error"
        with (
            patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="scheduler error"),
        ):
            adapter._local_list_jobs()

    def test_partial_pipe_line_skipped(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345|RUNNING\nincomplete_line_only\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            jobs = adapter._local_list_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "12345"

    def test_uses_correct_squeue_format(self, adapter):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["run_kwargs"] = kwargs
            return mock_result

        with patch("general_ludd.infra.slurm.subprocess.run", side_effect=fake_run):
            adapter._local_list_jobs()

        args = captured["args"]
        assert args[0] == "squeue"
        assert "--me" in args
        assert "--format=%i|%T" in args
        assert "--noheader" in args
        assert captured["run_kwargs"]["timeout"] == 30


# ========================================================================== #
# _validate_submit_params — coverage of ALL param families
# ========================================================================== #


class TestValidateSubmitParams:
    def test_account_validation(self):
        adapter = SlurmAdapter()
        with patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
            adapter.submit("echo hi", account="--evil")
        run.assert_not_called()

    def test_qos_validation(self):
        adapter = SlurmAdapter()
        with patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
            adapter.submit("echo hi", qos="-high")
        run.assert_not_called()

    def test_gpus_validation(self):
        adapter = SlurmAdapter()
        with patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
            adapter.submit("echo hi", gpus="evil\n#SBATCH --x=y")
        run.assert_not_called()

    def test_memory_validation(self):
        adapter = SlurmAdapter()
        with patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
            adapter.submit("echo hi", memory="-16G")
        run.assert_not_called()

    def test_all_params_accept_valid(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 1\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            job_id = adapter.submit(
                "echo hi",
                job_name="valid_job",
                partition="gpu",
                cpus_per_task=2,
                gpus="A100:2",
                memory="32G",
                time_limit="02:00:00",
                account="my_acct",
                qos="normal",
                output="/tmp/%j.out",
            )
        assert job_id == "1"

    def test_none_params_skipped(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 7\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            adapter._validate_submit_params(
                job_name=None,
                partition=None,
                gpus=None,
                memory=None,
                time_limit=None,
                account=None,
                qos=None,
                output=None,
                extra_args=None,
            )


# ========================================================================== #
# _remote_submit — REST submit path
# ========================================================================== #


class TestRemoteSubmit:
    def test_success_from_job_id_field(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"job_id": 999}
        with patch.object(adapter, "_request", return_value=mock_resp):
            job_id = adapter._remote_submit("echo hi")
        assert job_id == "999"

    def test_success_from_job_submit_user_msg(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"job_submit_user_msg": {"job_id": "42"}}
        with patch.object(adapter, "_request", return_value=mock_resp):
            job_id = adapter._remote_submit("echo hi")
        assert job_id == "42"

    def test_success_with_full_params(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"job_id": 5}
        captured = {}

        def record_req(method, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs.get("json", {})
            return mock_resp

        with patch.object(adapter, "_request", side_effect=record_req):
            job_id = adapter._remote_submit(
                "echo hi",
                job_name="test",
                partition="gpu",
                cpus_per_task=4,
                gpus="2",
                memory="16G",
                time_limit="01:00:00",
                account="proj123",
                qos="high",
            )
        assert job_id == "5"
        assert captured["url"].endswith("/job/submit")
        script = captured["payload"]["script"]
        assert "#SBATCH --job-name=test" in script
        assert "#SBATCH --partition=gpu" in script
        assert "#SBATCH --gres=gpu:2" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --account=proj123" in script
        assert "#SBATCH --qos=high" in script
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=01:00:00" in script
        assert "echo hi" in script

    def test_no_job_id_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        with (
            patch.object(adapter, "_request", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Could not parse job_id"),
        ):
            adapter._remote_submit("echo hi")

    def test_non_200_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        with (
            patch.object(adapter, "_request", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Slurm REST submit failed"),
        ):
            adapter._remote_submit("echo hi")


# ========================================================================== #
# _remote_status — REST status path
# ========================================================================== #


class TestRemoteStatus:
    def test_found_running(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": [{"job_id": "42", "job_state": "RUNNING", "exit_code": None}]}
        with patch.object(adapter, "_request", return_value=mock_resp):
            info = adapter._remote_status("42")
        assert info.job_id == "42"
        assert info.state == SlurmJobState.RUNNING
        assert info.exit_code is None

    def test_not_found_returns_unknown(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        with patch.object(adapter, "_request", return_value=mock_resp):
            info = adapter._remote_status("999")
        assert info.state == SlurmJobState.UNKNOWN
        assert info.job_id == "999"

    def test_empty_jobs_array_returns_unknown(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": []}
        with patch.object(adapter, "_request", return_value=mock_resp):
            info = adapter._remote_status("42")
        assert info.state == SlurmJobState.UNKNOWN

    def test_non_200_non_404_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 503
        mock_resp.text = "overloaded"
        with (
            patch.object(adapter, "_request", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Slurm REST status failed"),
        ):
            adapter._remote_status("42")


# ========================================================================== #
# _remote_cancel — REST cancel path
# ========================================================================== #


class TestRemoteCancel:
    def test_200_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        with patch.object(adapter, "_request", return_value=mock_resp):
            adapter._remote_cancel("42")  # no exception

    def test_204_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 204
        with patch.object(adapter, "_request", return_value=mock_resp):
            adapter._remote_cancel("42")  # no exception

    def test_non_success_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        with (
            patch.object(adapter, "_request", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Slurm REST cancel failed"),
        ):
            adapter._remote_cancel("42")


# ========================================================================== #
# _remote_available — ping endpoint
# ========================================================================== #


class TestRemoteAvailable:
    def test_ping_ok(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp):
            assert adapter._remote_available() is True

    def test_ping_non_200(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 503
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp):
            assert adapter._remote_available() is False

    def test_ping_connection_error(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch("general_ludd.infra.slurm.httpx.get", side_effect=httpx.ConnectError("refused")):
            assert adapter._remote_available() is False

    def test_ping_timeout(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch("general_ludd.infra.slurm.httpx.get", side_effect=httpx.TimeoutException("too slow")):
            assert adapter._remote_available() is False

    def test_ping_uses_correct_url(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820/")
        with patch("general_ludd.infra.slurm.httpx.get") as get:
            get.return_value = MagicMock(spec=httpx.Response, status_code=200)
            adapter._remote_available()
        get.assert_called_once()
        url = get.call_args[0][0]
        assert "/slurm/v0.0.40/ping" in url


# ========================================================================== #
# _remote_list_jobs — REST jobs list
# ========================================================================== #


class TestRemoteListJobs:
    def test_empty_list(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": []}
        with patch.object(adapter, "_request", return_value=mock_resp):
            assert adapter._remote_list_jobs() == []

    def test_multiple_jobs(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobs": [
                {"job_id": "1", "job_state": "RUNNING"},
                {"job_id": "2", "job_state": "PENDING"},
            ]
        }
        with patch.object(adapter, "_request", return_value=mock_resp):
            jobs = adapter._remote_list_jobs()
        assert len(jobs) == 2
        assert jobs[0].job_id == "1"
        assert jobs[1].job_id == "2"

    def test_non_200_raises(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with (
            patch.object(adapter, "_request", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Slurm REST list failed"),
        ):
            adapter._remote_list_jobs()


# ========================================================================== #
# SlurmAdapter routing — submit / status / cancel / available / list_jobs
# ========================================================================== #


class TestAdapterRouting:
    def test_submit_remote_when_remote(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch.object(adapter, "_remote_submit", return_value="42") as rm:
            job_id = adapter.submit("echo hi")
        rm.assert_called_once()
        assert job_id == "42"

    def test_submit_local_when_no_url(self):
        adapter = SlurmAdapter()
        with patch.object(adapter, "_local_submit", return_value="42") as lm:
            job_id = adapter.submit("echo hi")
        lm.assert_called_once()
        assert job_id == "42"

    def test_status_remote_when_remote(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch.object(adapter, "_remote_status", return_value=SlurmJobInfo("42", SlurmJobState.RUNNING)) as rm:
            info = adapter.status("42")
        rm.assert_called_once_with("42")
        assert info.state == SlurmJobState.RUNNING

    def test_cancel_remote_when_remote(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch.object(adapter, "_remote_cancel") as rm:
            adapter.cancel("42")
        rm.assert_called_once_with("42")

    def test_available_remote_when_remote(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch.object(adapter, "_remote_available", return_value=True) as rm:
            assert adapter.available() is True
        rm.assert_called_once()

    def test_list_jobs_remote_when_remote(self):
        adapter = SlurmAdapter(api_url="http://slurm:6820")
        with patch.object(adapter, "_remote_list_jobs", return_value=[]) as rm:
            assert adapter.list_jobs() == []
        rm.assert_called_once()


# ========================================================================== #
# SlurmJobMonitor — resolve_hourly_rate and edge cases
# ========================================================================== #


class TestMonitorResolveHourlyRate:
    def test_config_rate_takes_priority(self):
        config = SlurmJobConfig(hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = None
        monitor = SlurmJobMonitor(adapter, "1", config)
        assert monitor._resolve_hourly_rate() == 5.0

    def test_falls_back_to_pricing_module(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = None
        monitor = SlurmJobMonitor(adapter, "1", config)
        with patch("general_ludd.infra.pricing.infra_cost_usd", return_value=0.75) as mock_cost:
            rate = monitor._resolve_hourly_rate()
        mock_cost.assert_called_once_with("gpu_second", 3600.0)
        assert rate == 0.75


class TestMonitorDoubleStart:
    def test_start_twice_uses_same_thread(self):
        config = SlurmJobConfig()
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.COMPLETED)
        adapter.elapsed_seconds.return_value = 0.0
        monitor = SlurmJobMonitor(adapter, "1", config, poll_interval=0.01)
        monitor.start()
        t1 = monitor._thread
        monitor.start()
        assert monitor._thread is t1
        monitor.stop()


class TestMonitorTerminalStates:
    @pytest.mark.parametrize("state", [SlurmJobState.TIMEOUT, SlurmJobState.NODE_FAIL])
    def test_all_terminal_stop_poll(self, state):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=1.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 100.0
        adapter.status.return_value = SlurmJobInfo("1", state)
        monitor = SlurmJobMonitor(adapter, "1", config)
        result = monitor._poll()
        assert result is False

    def test_preempted_is_not_terminal(self):
        # PREEMPTED jobs may be requeued; _TERMINAL_STATES excludes PREEMPTED
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=1.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.elapsed_seconds.return_value = 100.0
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.PREEMPTED)
        monitor = SlurmJobMonitor(adapter, "1", config)
        result = monitor._poll()
        assert result is True


class TestMonitorCostEdgeCases:
    def test_cost_near_boundary_at_limit_not_exceeding(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=10.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 3599.0  # $9.997... → < $10
        monitor = SlurmJobMonitor(adapter, "1", config)
        monitor._poll()
        assert not monitor.cancelled

    def test_cost_near_boundary_just_exceeding(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=10.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 3601.0  # $10.002... → > $10
        monitor = SlurmJobMonitor(adapter, "1", config)
        monitor._poll()
        assert monitor.cancelled
        assert monitor.cancel_reason == SlurmJobMonitor.CANCEL_REASON_COST

    def test_elapsed_none_does_not_crash_cost_check(self):
        config = SlurmJobConfig(max_cost_usd=10.0, hourly_rate_usd=5.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = None
        monitor = SlurmJobMonitor(adapter, "1", config)
        monitor._poll()
        # cost_incurred computed as None / 3600 * rate → should use default 0.0
        assert monitor.cost_incurred == 0.0
        assert not monitor.cancelled


class TestMonitorIdleBoundary:
    def test_idle_exactly_at_timeout_boundary(self):
        config = SlurmJobConfig(idle_timeout_minutes=15.0)
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.status.return_value = SlurmJobInfo("1", SlurmJobState.RUNNING)
        adapter.elapsed_seconds.return_value = 0.0
        activity = MagicMock(return_value=False)
        monitor = SlurmJobMonitor(adapter, "1", config, activity_checker=activity)

        with patch("general_ludd.infra.slurm.time") as mock_time:
            mock_time.time.return_value = 1000.0
            monitor._poll()  # sets _idle_since = 1000.0

            # exactly 15 min later
            mock_time.time.return_value = 1000.0 + (15 * 60)  # 1900.0
            monitor._poll()
            assert monitor.cancelled


class TestMonitorExplicitCancelReasonConstants:
    def test_cost_constant(self):
        assert SlurmJobMonitor.CANCEL_REASON_COST == "cost_cap"

    def test_idle_constant(self):
        assert SlurmJobMonitor.CANCEL_REASON_IDLE == "idle_timeout"
