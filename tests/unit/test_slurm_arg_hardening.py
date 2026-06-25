"""Argument-injection hardening for the local Slurm CLI adapter (#68).

The local paths build argv for ``sbatch``/``sacct``/``scancel``/``squeue`` and
embed config-derived values (job id, job name, partition, account, time limit,
output path, script path) into either argv or a generated batch script.

A value like ``"-A evil"``, ``"; rm -rf /"`` or a leading-dash token must NOT be
able to:

* smuggle an extra flag into argv (e.g. a job id that begins with ``-``), or
* inject an extra ``#SBATCH`` directive / arbitrary shell into the generated
  script (e.g. a job name containing a newline or shell metacharacters).

These tests mock ``subprocess.run`` — no real Slurm is invoked. They assert the
adapter validates fail-closed (raises ``ValueError``) on hostile input and that
a normal submit/status/cancel produces the expected list-form argv with a ``--``
end-of-options separator before positional args.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.infra.slurm import SlurmAdapter


@pytest.fixture
def adapter() -> SlurmAdapter:
    return SlurmAdapter()


def _ok_run(stdout: str = "", returncode: int = 0) -> mock.Mock:
    result = mock.Mock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


# --------------------------------------------------------------------------- #
# job id (sacct / scancel) — numeric only, no flag smuggling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_job_id",
    [
        "-A evil",
        "--help",
        "-1234",
        "1234; rm -rf /",
        "1234 evil",
        "$(whoami)",
        "1234`id`",
        "",
        "abc",
    ],
)
def test_status_rejects_injection_job_id(adapter: SlurmAdapter, bad_job_id: str) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.status(bad_job_id)
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_job_id",
    ["-A evil", "--help", "-1234", "1234; rm -rf /", "$(whoami)", "abc", ""],
)
def test_cancel_rejects_injection_job_id(adapter: SlurmAdapter, bad_job_id: str) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.cancel(bad_job_id)
    run.assert_not_called()


def test_status_normal_job_id_argv(adapter: SlurmAdapter) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run:
        run.return_value = _ok_run(stdout="1234|COMPLETED|0\n")
        adapter.status("1234")
    argv = run.call_args[0][0]
    assert isinstance(argv, list)
    assert argv[0] == "sacct"
    # the (validated, numeric) job id is bound to its flag as ``--jobs=<id>`` so
    # it can never be parsed as a standalone option; it never rides as a bare
    # positional that a leading-dash value could turn into an extra flag.
    assert "--jobs=1234" in argv
    assert "1234" not in argv  # no bare positional form
    assert run.call_args.kwargs.get("shell", False) is False


def test_cancel_normal_job_id_argv(adapter: SlurmAdapter) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run:
        run.return_value = _ok_run()
        adapter.cancel("1234")
    argv = run.call_args[0][0]
    assert isinstance(argv, list)
    assert argv[0] == "scancel"
    assert "--" in argv
    assert argv[-1] == "1234"


# --------------------------------------------------------------------------- #
# job name / partition (sbatch script directives) — safe charset, no injection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_name",
    [
        "-A evil",
        "evil\n#SBATCH --partition=hijack",
        "evil; rm -rf /",
        "$(whoami)",
        "evil`id`",
        "evil name",
        "evil$IFS",
    ],
)
def test_submit_rejects_injection_job_name(adapter: SlurmAdapter, bad_name: str) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.submit("echo hi", job_name=bad_name)
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_partition",
    [
        "-A evil",
        "gpu\n#SBATCH --account=victim",
        "gpu; rm -rf /",
        "$(whoami)",
        "gpu name",
    ],
)
def test_submit_rejects_injection_partition(adapter: SlurmAdapter, bad_partition: str) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.submit("echo hi", partition=bad_partition)
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_time",
    ["10:00; rm -rf /", "-1", "10:00\n#SBATCH --x=y", "$(id)"],
)
def test_submit_rejects_injection_time_limit(adapter: SlurmAdapter, bad_time: str) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.submit("echo hi", time_limit=bad_time)
    run.assert_not_called()


def test_submit_rejects_injection_extra_args(adapter: SlurmAdapter) -> None:
    # extra_args still flow into argv; each entry must be a non-empty string
    # with no embedded newline that could be (mis)used to smuggle directives.
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.submit("echo hi", extra_args=["--ok", "bad\nflag"])
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_output",
    [
        "/tmp/out.log\n#SBATCH --partition=hijack",
        "/tmp/out.log\r#SBATCH --account=victim",
        "/tmp/out\x00.log",
    ],
)
def test_submit_rejects_newline_nul_in_output(adapter: SlurmAdapter, bad_output: str) -> None:
    # The ``#SBATCH --output=`` path is embedded directly into a directive line;
    # a newline/CR would inject an extra ``#SBATCH`` directive and a NUL would
    # truncate the path. All three must be rejected fail-closed before sbatch runs.
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run, pytest.raises(ValueError):
        adapter.submit("echo hi", output=bad_output)
    run.assert_not_called()


def test_submit_normal_output_path_passes(adapter: SlurmAdapter) -> None:
    # A legitimate output path (slashes, %-patterns, dots, spaces) is accepted and
    # rides as a single ``#SBATCH --output=`` directive with no injected extras.
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run:
        run.return_value = _ok_run(stdout="Submitted batch job 4242\n")
        job_id = adapter.submit("echo hi", output="/var/log/slurm/job_%j.out")
    assert job_id == "4242"
    script = run.call_args.kwargs["input"]
    if isinstance(script, bytes):
        script = script.decode()
    assert "#SBATCH --output=/var/log/slurm/job_%j.out" in script
    assert script.count("#SBATCH --output") == 1


def test_submit_normal_builds_list_argv_and_script(adapter: SlurmAdapter) -> None:
    with mock.patch("general_ludd.infra.slurm.subprocess.run") as run:
        run.return_value = _ok_run(stdout="Submitted batch job 4242\n")
        job_id = adapter.submit(
            "echo hi",
            job_name="my_job-1",
            partition="gpu",
            time_limit="01:00:00",
        )
    assert job_id == "4242"
    argv = run.call_args[0][0]
    assert isinstance(argv, list)
    assert argv[0] == "sbatch"
    # never invoke a shell
    assert run.call_args.kwargs.get("shell", False) is False
    # the generated script carries exactly the directives we set, no extras
    script = run.call_args.kwargs["input"]
    if isinstance(script, bytes):
        script = script.decode()
    assert "#SBATCH --job-name=my_job-1" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --time=01:00:00" in script
    # a single job-name directive — no injected duplicate
    assert script.count("#SBATCH --job-name") == 1
