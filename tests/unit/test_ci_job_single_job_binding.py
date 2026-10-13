"""Native Make contracts for exact-job ownership before hosted log context."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
NATIVE_ROOT = Path(compose_makefile.__code__.co_filename).resolve().parents[1]
RUN_ID = "38467206828"
JOB_ID = "115537016804"
JOB_ENDPOINT = f"repos/sandboxcom/gludd/actions/jobs/{JOB_ID}"
OWNERSHIP_QUERY = f"select(.id == {JOB_ID} and .run_id == {RUN_ID}) | .id"

FAKE_GH = """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
trace = Path(os.environ['GLUDD_JOB_BINDING_TRACE'])
with trace.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + '\\n')
mode = os.environ['GLUDD_JOB_BINDING_MODE']
job = os.environ['GLUDD_JOB_BINDING_JOB']
run = os.environ['GLUDD_JOB_BINDING_RUN']
endpoint = 'repos/sandboxcom/gludd/actions/jobs/' + job
if args[:2] == ['run', 'view']:
    print('HTTP 502: run-job fan-out unavailable', file=sys.stderr)
    raise SystemExit(42)
if args[:3] != ['api', '--method', 'GET']:
    raise SystemExit(90)
if endpoint in args:
    if mode == 'lookup-network':
        print('HTTP 502: exact-job metadata unavailable', file=sys.stderr)
        raise SystemExit(23)
    if mode == 'lookup-malformed':
        print('failed to parse JSON response', file=sys.stderr)
        raise SystemExit(24)
    expected = 'select(.id == ' + job + ' and .run_id == ' + run + ') | .id'
    if '--jq' not in args or args[args.index('--jq') + 1] != expected:
        print('ownership query must bind both job id and parent run', file=sys.stderr)
        raise SystemExit(91)
    if mode not in ('wrong-run', 'wrong-job', 'missing-run', 'missing-id', 'lookup-empty'):
        print(job)
    raise SystemExit(0)
if endpoint + '/logs' in args:
    if mode == 'log-network':
        print('HTTP 403: log download denied', file=sys.stderr)
        raise SystemExit(29)
    if mode == 'log-empty':
        raise SystemExit(0)
    if mode == 'pattern-missing':
        print('all green')
    elif mode == 'bounded':
        for index in range(30):
            print('FAILED ' + ('界' * 3000) + ' ' + str(index))
    else:
        print('alpha\\nFAILED focused assertion\\nomega\\nFAILED later assertion')
    raise SystemExit(0)
raise SystemExit(92)
"""


@dataclass(frozen=True)
class NativeContextResult:
    """One native recipe result and its exact fake-client invocation trace."""

    result: subprocess.CompletedProcess[str]
    calls: list[list[str]]
    resource_root: Path


def _native_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "match",
    validate_only: str = "0",
    extra_assignments: tuple[str, ...] = (),
) -> NativeContextResult:
    """Execute the existing Make recipe with a disposable authenticated CLI fake."""
    source = (ROOT / "make/30-ci-and-release.mk").read_text(encoding="utf-8")
    recipe_body = source.split("ci-job-failure-context:", 1)[1].split("\n\n", 1)[0]
    recipe = tmp_path / "Makefile"
    recipe.write_text(
        "CI_JOB_CONTEXT_VALIDATE_ONLY ?= 0\nci-job-failure-context:" + recipe_body + "\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("resource_arbiter.py", "ci_shards_log_context.py"):
        (scripts / name).write_bytes((NATIVE_ROOT / "scripts" / name).read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    fake_gh.chmod(0o755)
    trace = tmp_path / "gh-trace.jsonl"
    resource_root = tmp_path / "owned-resources"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GLUDD_JOB_BINDING_TRACE", str(trace))
    monkeypatch.setenv("GLUDD_JOB_BINDING_MODE", mode)
    monkeypatch.setenv("GLUDD_JOB_BINDING_RUN", RUN_ID)
    monkeypatch.setenv("GLUDD_JOB_BINDING_JOB", JOB_ID)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resource_root))
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"):
        environment.pop(name, None)
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(recipe),
            "ci-job-failure-context", f"PYTHON={sys.executable}",
            f"RUN={RUN_ID}", f"JOB={JOB_ID}", "PATTERN=FAILED", "BEFORE=1", "AFTER=1",
            "MAX_MATCHES=1", f"CI_JOB_CONTEXT_VALIDATE_ONLY={validate_only}",
            *extra_assignments,
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=30,
    )
    calls: list[list[str]] = []
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            value: object = json.loads(line)
            assert isinstance(value, list)
            arguments: list[str] = []
            for item in value:
                assert isinstance(item, str)
                arguments.append(item)
            calls.append(arguments)
    return NativeContextResult(result, calls, resource_root)


def test_native_lookup_binds_exact_job_to_parent_run_before_log_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _native_context(tmp_path, monkeypatch)

    assert observed.result.returncode == 0, observed.result.stdout + observed.result.stderr
    assert len(observed.calls) == 2
    assert observed.calls[0] == [
        "api", "--method", "GET", "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2026-03-10", JOB_ENDPOINT, "--jq", OWNERSHIP_QUERY,
    ]
    assert observed.calls[1] == [
        "api", "--method", "GET", "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2026-03-10", JOB_ENDPOINT + "/logs",
    ]
    assert ">2: FAILED focused assertion" in observed.result.stdout
    assert " 1: alpha" in observed.result.stdout
    assert " 3: omega" in observed.result.stdout
    assert "FAILED later assertion" not in observed.result.stdout
    assert not list(observed.resource_root.rglob("ci-job-*.log.*"))


@pytest.mark.parametrize("mode", ["wrong-run", "wrong-job", "missing-run", "missing-id", "lookup-empty"])
def test_unbound_job_never_downloads_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    observed = _native_context(tmp_path, monkeypatch, mode=mode)

    assert observed.result.returncode != 0
    assert f"job {JOB_ID} is not bound to run {RUN_ID}" in observed.result.stdout
    assert len(observed.calls) == 1
    assert JOB_ENDPOINT in observed.calls[0]
    assert not list(observed.resource_root.rglob("ci-job-*.log.*"))


@pytest.mark.parametrize(("mode", "returncode"), [("lookup-network", 23), ("lookup-malformed", 24)])
def test_metadata_failure_preserves_original_client_exit_and_skips_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    returncode: int,
) -> None:
    observed = _native_context(tmp_path, monkeypatch, mode=mode)

    assert observed.result.returncode != 0
    assert f"job lookup failed rc={returncode}" in observed.result.stdout
    assert f"Error {returncode}" in observed.result.stderr
    assert len(observed.calls) == 1
    assert not list(observed.resource_root.rglob("ci-job-*.log.*"))


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("log-network", "log fetch failed rc=29"),
        ("log-empty", "downloaded log is empty"),
        ("pattern-missing", "pattern not found: FAILED"),
    ],
)
def test_log_failures_stay_terminal_and_cleanup_owned_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    message: str,
) -> None:
    observed = _native_context(tmp_path, monkeypatch, mode=mode)

    assert observed.result.returncode != 0
    assert message in observed.result.stdout
    assert len(observed.calls) == 2
    assert not list(observed.resource_root.rglob("ci-job-*.log.*"))


def test_validate_only_preserves_the_existing_mutation_free_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _native_context(tmp_path, monkeypatch, validate_only="1")

    assert observed.result.returncode == 0, observed.result.stdout + observed.result.stderr
    assert f"CI-JOB-CONTEXT VALIDATED run={RUN_ID} job={JOB_ID}" in observed.result.stdout
    assert observed.calls == []
    assert not observed.resource_root.exists()


def test_same_context_collector_keeps_utf8_line_and_total_display_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _native_context(
        tmp_path,
        monkeypatch,
        mode="bounded",
        extra_assignments=("BEFORE=0", "AFTER=30", "MAX_MATCHES=30"),
    )

    assert observed.result.returncode == 0, observed.result.stdout + observed.result.stderr
    assert len(observed.result.stdout.encode("utf-8")) <= 32_768
    assert all(len(line.encode("utf-8")) + 1 <= 2_048 for line in observed.result.stdout.splitlines())
    assert "output truncated" in observed.result.stdout
    assert "line truncated" in observed.result.stdout
    assert len(observed.calls) == 2


def test_context_collector_invalid_bounds_still_fail_after_authenticated_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _native_context(tmp_path, monkeypatch, extra_assignments=("MAX_MATCHES=0",))

    assert observed.result.returncode != 0
    assert "max_matches must be >= 1" in observed.result.stdout
    assert len(observed.calls) == 2
    assert not list(observed.resource_root.rglob("ci-job-*.log.*"))
