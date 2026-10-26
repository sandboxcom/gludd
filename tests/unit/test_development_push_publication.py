"""Confined native Git proof for guarded development publication delegation."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / "make/20-recovery-and-git.mk"
GUARDRAILS = ROOT / "make/50-development-and-guardrails.mk"
RELEASE = ROOT / "make/30-ci-and-release.mk"
FOUNDATION = ROOT / "make/00-foundation.mk"
REAL_GIT = shutil.which("git")
REAL_MAKE = shutil.which("make")
BRANCH_INPUT = "PUBLISH_EXPECTED_BRANCH"
SHA_INPUT = "PUBLISH_EXPECTED_SHA"
ORIGINAL_GUARDS = (
    "check-clean-tree",
    "_test-disabled-guard",
    "_push-rate-guard",
    "_stash-before-push-guard",
    "_pull-before-push-guard",
    "_ci-verdict-history-guard",
    "_pre-commit-stash-audit",
    "_ci-restart-cap",
)


def _target(source: str, name: str) -> str:
    start = source.index(f"\n{name}:") + 1
    return source[start : source.index("\n\n", start)]


def _definition(source: str, name: str) -> str:
    start = source.index(f"define {name}\n")
    return source[start : source.index("\nendef", start) + len("\nendef")]


def _early_inputs() -> str:
    source = FOUNDATION.read_text(encoding="utf-8")
    marker = "# Keep expected publication identity literal before startup/export expansion."
    if marker not in source:
        return ""
    start = source.index(marker)
    return source[start : source.index("\n\n", start)] + "\n"


def _inspection_inputs(source: str) -> str:
    marker = ".PHONY: batch-push git-push-sandboxcom\n"
    if marker in source:
        start = source.index(marker)
        foundation = FOUNDATION.read_text(encoding="utf-8")
        provider_start = foundation.index("# Shared native Make inspection grammar.")
        provider_end = foundation.index("# End shared native Make inspection grammar.", provider_start)
        provider = foundation[provider_start:provider_end] + "# End shared native Make inspection grammar.\n"
        return provider + source[start : source.index("\ngit-push-sandboxcom:", start)] + "\n"
    return "\n".join(line for line in source.splitlines() if line.startswith(".PHONY: development-push")) + "\n"


class NativeRow(TypedDict):
    kind: str
    args: list[str]
    branch: NotRequired[str | None]
    force: NotRequired[str | None]
    force_push: NotRequired[str | None]
    carrier: NotRequired[str | None]


@dataclass
class DevelopmentFixture:
    root: Path
    remote: Path
    makefile: Path
    calls: Path
    key: Path
    environment: dict[str, str] = field(repr=False)
    sha: str

    def git(
        self,
        *arguments: str,
        bare: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert REAL_GIT is not None
        return subprocess.run(
            [REAL_GIT, *arguments],
            cwd=self.remote if bare else self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=check,
            timeout=10,
        )

    def rows(self, kind: str = "") -> list[NativeRow]:
        if not self.calls.exists():
            return []
        rows: list[NativeRow] = []
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            row: object = json.loads(line)
            assert isinstance(row, dict) and all(isinstance(key, str) for key in row)
            if not kind or row.get("kind") == kind:
                rows.append(cast("NativeRow", row))
        return rows

    def invoke(
        self,
        goal: str = "development-push",
        *arguments: str,
        mode: str = "",
        early_environment: bool = False,
        environment: dict[str, str] | None = None,
        makefile: Path | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert REAL_MAKE is not None
        return subprocess.run(
            [
                REAL_MAKE,
                "--no-print-directory",
                *([mode] if mode else []),
                *(["-e"] if early_environment else []),
                "-f",
                str(makefile or self.makefile),
                goal,
                *arguments,
            ],
            cwd=cwd or self.root,
            env=environment or self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def remote_sha(self) -> str:
        return self.git("rev-parse", "refs/heads/development", bare=True).stdout.strip()


@pytest.fixture
def development_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DevelopmentFixture:
    assert REAL_GIT is not None and REAL_MAKE is not None
    root = tmp_path / "owned repository"
    root.mkdir()
    remote = tmp_path / "owned remote.git"
    remote.mkdir()
    binaries = tmp_path / "owned binaries"
    binaries.mkdir()
    home = tmp_path / "owned home"
    home.mkdir()
    calls = tmp_path / "native-calls.jsonl"
    key = tmp_path / "owned-key"
    key.write_text("fixture only, never a credential\n", encoding="utf-8")
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS"):
        monkeypatch.delenv(name, raising=False)
    for name in tuple(os.environ):
        if name.startswith(("GIT_", "GH_", "GITHUB_")):
            monkeypatch.delenv(name, raising=False)
    for name, value in {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GH_CONFIG_DIR": str(home / "gh"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        "DEVELOPMENT_CALLS": str(calls),
        "DEVELOPMENT_REMOTE": str(remote),
        "DEVELOPMENT_CARRIER": "retained-unrelated-carrier",
    }.items():
        monkeypatch.setenv(name, value)
    environment = os.environ.copy()
    fixture = DevelopmentFixture(root, remote, root / "Makefile", calls, key, environment, "")
    fixture.git("-c", "init.templateDir=", "init", "--bare", bare=True)
    fixture.git("-c", "init.templateDir=", "init", "-b", "development")
    fixture.git("config", "user.name", "Development publication fixture")
    fixture.git("config", "user.email", "development@fixture.invalid")
    note = root / "tracked.txt"
    note.write_text("owned old source\n", encoding="utf-8")
    fixture.git("add", "tracked.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned old")
    fixture.git("remote", "add", "sandboxcom", str(remote))
    fixture.git("push", "--quiet", "-u", "sandboxcom", "development")
    note.write_text("owned candidate source\n", encoding="utf-8")
    fixture.git("add", "tracked.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned candidate")
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.git("remote", "set-url", "sandboxcom", "https://github.com/sandboxcom/gludd.git")
    (root / ".git/info").mkdir(exist_ok=True)
    (root / ".git/info/exclude").write_text("*\n", encoding="utf-8")
    (root / ".git/hooks").mkdir(exist_ok=True)
    recorder = root / "native-boundary.py"
    recorder.write_text(
        "import json, os, pathlib, subprocess, sys\n"
        "kind, *args = sys.argv[1:]\n"
        "path = pathlib.Path(os.environ['DEVELOPMENT_CALLS'])\n"
        "with path.open('a') as output:\n"
        "    output.write(json.dumps({'kind': kind, 'args': args,\n"
        "        'branch': os.environ.get('BRANCH'), 'force': os.environ.get('FORCE'),\n"
        "        'force_push': os.environ.get('GLUDD_FORCE_PUSH'),\n"
        "        'carrier': os.environ.get('DEVELOPMENT_CARRIER')}) + '\\n')\n"
        "phase = kind + (':' + args[0] if kind == 'guard' else '')\n"
        "if phase == os.environ.get('DEVELOPMENT_MUTATE_PHASE'):\n"
        "    mutation = os.environ.get('DEVELOPMENT_MUTATION')\n"
        "    if mutation == 'head':\n"
        "        command = ['-c', 'commit.gpgSign=false', 'commit', '--allow-empty',\n"
        "                   '--quiet', '-m', 'owned identity shift']\n"
        "    elif mutation == 'branch':\n"
        "        command = ['checkout', '--quiet', '-b', 'owned-shifted-branch']\n"
        "    else: raise AssertionError(mutation)\n"
        f"    subprocess.run([{REAL_GIT!r}, *command], check=True, env=os.environ.copy())\n"
        "if phase == os.environ.get('DEVELOPMENT_FAIL_PHASE'):\n"
        "    code = int(os.environ.get('DEVELOPMENT_FAIL_STATUS', '83'))\n"
        "    print('native-fixture-' + phase + '-' + str(code), file=sys.stderr)\n"
        "    raise SystemExit(code)\n",
        encoding="utf-8",
    )
    git_adapter = binaries / "git"
    git_adapter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "path = pathlib.Path(os.environ['DEVELOPMENT_CALLS'])\n"
        "with path.open('a') as output:\n"
        "    output.write(json.dumps({'kind': 'git', 'args': args,\n"
        "        'ssh_command': os.environ.get('GIT_SSH_COMMAND')}) + '\\n')\n"
        "rows = [json.loads(line) for line in path.read_text().splitlines()]\n"
        "runtime = any(row['kind'] == 'runtime' for row in rows)\n"
        "fault = os.environ.get('DEVELOPMENT_GIT_FAULT', '')\n"
        "if args == ['branch', '--show-current'] and fault == ('post-branch' if runtime else 'branch'):\n"
        "    print('native-fixture-branch-74', file=sys.stderr); raise SystemExit(74)\n"
        "if args == ['rev-parse', '--verify', 'HEAD^{commit}'] and fault == ('post-head' if runtime else 'head'):\n"
        "    print('native-fixture-head-75', file=sys.stderr); raise SystemExit(75)\n"
        "if args == ['branch', '--set-upstream-to=sandboxcom/development', 'development'] and fault == 'tracking':\n"
        "    print('native-fixture-tracking-76', file=sys.stderr); raise SystemExit(76)\n"
        "if args[0] == 'push':\n"
        "    with path.open('a') as output:\n"
        "        output.write(json.dumps({'kind': 'transport', 'args': args}) + '\\n')\n"
        "    code = int(os.environ.get('DEVELOPMENT_PUSH_STATUS', '0'))\n"
        "    if code:\n"
        "        print('native-fixture-transport-' + str(code), file=sys.stderr)\n"
        "        raise SystemExit(code)\n"
        "if args[0] == 'ls-remote' and os.environ.get('DEVELOPMENT_VERIFY_STATUS'):\n"
        "    code = int(os.environ['DEVELOPMENT_VERIFY_STATUS'])\n"
        "    print('native-fixture-verify-' + str(code), file=sys.stderr); raise SystemExit(code)\n"
        "if args[0] == 'push' and os.environ.get('DEVELOPMENT_MUTATE_TRANSPORT'):\n"
        f"    subprocess.run([{REAL_GIT!r}, '-c', 'commit.gpgSign=false', 'commit', '--allow-empty',\n"
        "                    '--quiet', '-m', 'owned transport identity shift'], check=True,\n"
        "                   env=os.environ.copy())\n"
        "if args[0] in ('push', 'fetch', 'ls-remote'):\n"
        "    prefix = 'url.' + os.environ['DEVELOPMENT_REMOTE'] + '.insteadOf='\n"
        "    args = ['-c', prefix + 'https://github.com/sandboxcom/gludd.git',\n"
        "            '-c', prefix + 'git@github.com:sandboxcom/gludd.git', *args]\n"
        f"raise SystemExit(subprocess.run([{REAL_GIT!r}, *args], check=False, env=os.environ.copy()).returncode)\n",
        encoding="utf-8",
    )
    git_adapter.chmod(0o700)
    gh = binaries / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with pathlib.Path(os.environ['DEVELOPMENT_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps({'kind': 'auth', 'args': args}) + '\\n')\n"
        "assert args == ['auth', 'status', '--active', '--hostname', 'github.com']\n"
        "code = int(os.environ.get('DEVELOPMENT_AUTH_STATUS', '0'))\n"
        "if code: print('native-fixture-auth-' + str(code), file=sys.stderr)\n"
        "raise SystemExit(code)\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    for name in ("ssh", "uv"):
        forbidden = binaries / name
        forbidden.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, os\n"
            f"pathlib.Path({str(tmp_path / 'forbidden-external-command')!r}).write_text({name!r})\n"
            "raise SystemExit(91)\n",
            encoding="utf-8",
        )
        forbidden.chmod(0o700)
    runner = shlex.quote(sys.executable) + " " + shlex.quote(str(recorder))
    hook = root / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexec " + runner + " hook\n", encoding="utf-8")
    hook.chmod(0o700)
    fixture.git("config", "--local", "credential.https://github.com.helper", "")
    fixture.git(
        "config",
        "--local",
        "--add",
        "credential.https://github.com.helper",
        "!" + shlex.quote(str(gh)) + " auth git-credential",
    )
    environment["PATH"] = str(binaries) + os.pathsep + environment.get("PATH", "")
    recovery = RECOVERY.read_text(encoding="utf-8")
    guardrails = GUARDRAILS.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")
    native = [_target(recovery, "git-push-sandboxcom"), _target(guardrails, "development-push")]
    guards = sorted(
        {name for block in native for name in block.splitlines()[0].partition(":")[2].split()}
        - {"_require-sandboxcom-git-auth"}
    )
    fixture.makefile.write_text(
        _early_inputs()
        + _inspection_inputs(recovery)
        + _inspection_inputs(guardrails)
        + f"MAKE := {REAL_MAKE} --no-print-directory -f {shlex.quote(str(fixture.makefile))}\n"
        + f"SYSTEM_PYTHON := {sys.executable}\nSSH_KEY := {key}\n"
        + ".PHONY: "
        + " ".join(guards)
        + " _runtime-publish-admission _record-push-verdict\n"
        + "".join(f"{name}:\n\t@{runner} guard {name}\n\n" for name in guards)
        + "\n\n".join(
            _definition(release, name)
            for name in (
                "_sandboxcom_publication_failure",
                "_sandboxcom_publication_remote_binding",
                "_sandboxcom_publication_gh_helper",
            )
        )
        + "\n\n"
        + "\n\n".join(
            _target(release, name)
            for name in (
                "require-sandboxcom-ssh-key",
                "_require-sandboxcom-git-auth",
                "verify-remote",
            )
        )
        + "\n\n"
        + "\n\n".join(native)
        + f"\n\n_runtime-publish-admission:\n\t@{runner} runtime\n"
        + f"\n_record-push-verdict:\n\t@{runner} verdict\n",
        encoding="utf-8",
    )
    return fixture


@pytest.mark.parametrize("publication_controller_e", [False, True])
def test_native_auth_helper_uses_controller_instead_of_application_python(
    development_repo: DevelopmentFixture,
    publication_controller_e: bool,
) -> None:
    """Credential metadata must execute in the installed controller runtime."""
    fixture = development_repo
    marker = fixture.root.parent / "owned-application-python-used"
    application = fixture.root.parent / "owned-application-python"
    application.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('unexpected application runtime', encoding='utf-8')\n"
        "raise SystemExit(79)\n",
        encoding="utf-8",
    )
    application.chmod(0o700)
    environment = dict(fixture.environment)
    environment["PYTHON"] = str(application)
    environment["SYSTEM_PYTHON"] = sys.executable
    config = fixture.root / ".git/config"
    before_config = config.read_bytes()
    result = fixture.invoke(
        "_require-sandboxcom-git-auth",
        environment=environment,
        early_environment=publication_controller_e,
    )
    assert not marker.exists(), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_bytes() == before_config
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")
    assert environment["PYTHON"] == str(application)
    assert environment["SYSTEM_PYTHON"] == sys.executable


def _install_native_recorder(fixture: DevelopmentFixture) -> tuple[Path, Path]:
    """Use actual source and state owner while keeping every persistent path confined."""
    runner = shlex.quote(sys.executable) + " " + shlex.quote(str(fixture.root / "native-boundary.py"))
    source = fixture.makefile.read_text(encoding="utf-8")
    stub = f"_record-push-verdict:\n\t@{runner} verdict"
    assert source.count(stub) == 1
    fixture.makefile.write_text(
        source.replace(stub, _target(GUARDRAILS.read_text(encoding="utf-8"), "_record-push-verdict")),
        encoding="utf-8",
    )
    scripts = fixture.root / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "scripts/ci_check_cooldown.py", scripts / "ci_check_cooldown.py")
    history = fixture.root.parent / "owned-history.json"
    restart = fixture.root.parent / "owned-restart-count"
    fixture.environment.update(
        {
            "GLUDD_CI_STATE_FILE": str(fixture.root.parent / "owned-check-state.json"),
            "GLUDD_CI_HISTORY_FILE": str(history),
            "GLUDD_CI_RESTART_COUNT_FILE": str(restart),
            "GLUDD_PUSH_STATE_FILE": str(fixture.root.parent / "owned-push-state.json"),
        }
    )
    return history, restart


@pytest.fixture
def feature_repo(development_repo: DevelopmentFixture) -> DevelopmentFixture:
    """Publish a real immutable workflow baseline, then branch an owned feature."""
    fixture = development_repo
    workflows = fixture.root / ".github/workflows"
    workflows.mkdir(parents=True)
    shutil.copy2(ROOT / ".github/workflows/build.yml", workflows / "build.yml")
    fixture.git("add", "-f", ".github/workflows/build.yml")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned workflow baseline")
    baseline = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.git("push", "--quiet", str(fixture.remote), baseline + ":refs/heads/master")
    fixture.git("checkout", "--quiet", "-b", "feature/owned-source")
    (fixture.root / "tracked.txt").write_text("owned feature source\n", encoding="utf-8")
    fixture.git("add", "tracked.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned feature")
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    scripts = fixture.root / "scripts"
    scripts.mkdir()
    for filename in (
        "ci_remote_head_guard.py",
        "ci_signal_exact_sha.py",
        "ci_check_cooldown.py",
        "workflow_state_guard.py",
    ):
        shutil.copy2(ROOT / "scripts" / filename, scripts / filename)
    source = fixture.makefile.read_text(encoding="utf-8")
    runner = shlex.quote(sys.executable) + " " + shlex.quote(str(fixture.root / "native-boundary.py"))
    stub = f"_record-push-verdict:\n\t@{runner} verdict"
    assert source.count(stub) == 1
    source = source.replace(stub, _target(GUARDRAILS.read_text(encoding="utf-8"), "_record-push-verdict"))
    pipelines = (ROOT / "make/95-collections-and-pipelines.mk").read_text(encoding="utf-8")
    fixture.makefile.write_text(
        source
        + "\n\n"
        + _target(FOUNDATION.read_text(encoding="utf-8"), ".PHONY")
        + "\n\n"
        + next(line for line in pipelines.splitlines() if line == ".PHONY: ci-trigger-committed-head")
        + "\n"
        + _target(pipelines, "ci-trigger-committed-head")
        + "\n\n"
        + _target(RELEASE.read_text(encoding="utf-8"), "ci-trigger")
        + "\n\n"
        + _target(RECOVERY.read_text(encoding="utf-8") + "\n\n", "_require-gh")
        + "\n",
        encoding="utf-8",
    )
    gh = Path(fixture.environment["PATH"].split(os.pathsep)[0]) / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "path = pathlib.Path(os.environ['DEVELOPMENT_CALLS'])\n"
        f"owned_git = {REAL_GIT!r}\n"
        "def native(*values):\n"
        "    return subprocess.run([owned_git, *values], check=True, capture_output=True,\n"
        "                          text=True, env=os.environ.copy())\n"
        "kind = ('auth' if args[:2] == ['auth', 'status'] else\n"
        "        ('dispatch' if args[:2] == ['workflow', 'run'] else 'lookup'))\n"
        "row = {'kind': kind, 'args': args}\n"
        "if kind == 'dispatch':\n"
        "    row['head_sha'] = native('rev-parse', 'HEAD').stdout.strip()\n"
        "with path.open('a') as output:\n"
        "    output.write(json.dumps(row) + '\\n')\n"
        "code = int(os.environ.get('FEATURE_GH_STATUS', '0'))\n"
        "if code and kind == os.environ.get('FEATURE_GH_FAILURE_PHASE', 'dispatch'):\n"
        "    print('owned-gh-failure-' + str(code), file=sys.stderr); raise SystemExit(code)\n"
        "if kind == 'auth': raise SystemExit(int(os.environ.get('DEVELOPMENT_AUTH_STATUS', '0')))\n"
        "assert '-R' in args and args[args.index('-R') + 1] == 'sandboxcom/gludd'\n"
        "if kind == 'dispatch':\n"
        "    assert args[:3] == ['workflow', 'run', 'Build and Release']\n"
        "    assert args[args.index('--ref') + 1] == os.environ['FEATURE_BRANCH']\n"
        "    raise SystemExit(0)\n"
        "assert args[:2] == ['run', 'list']\n"
        "sha = args[args.index('--commit') + 1]\n"
        "rows = [json.loads(line) for line in path.read_text().splitlines()]\n"
        "if os.environ.get('FEATURE_MUTATE_LOOKUP') and sum(row['kind'] == 'lookup' for row in rows) == 2:\n"
        "    native('-c', 'commit.gpgSign=false', 'commit', '--allow-empty', '--quiet',\n"
        "           '-m', 'owned lookup shift')\n"
        "    shifted = native('rev-parse', 'HEAD').stdout.strip()\n"
        "    native('-C', os.environ['DEVELOPMENT_REMOTE'], 'fetch', '--quiet', '--no-tags',\n"
        "           os.getcwd(), os.environ['FEATURE_BRANCH'])\n"
        "    native('-C', os.environ['DEVELOPMENT_REMOTE'], 'update-ref',\n"
        "           'refs/heads/' + os.environ['FEATURE_BRANCH'], shifted)\n"
        "published = [row['head_sha'] for row in rows if row['kind'] == 'dispatch']\n"
        "visible = os.environ.get('FEATURE_EXISTING_RUN') or bool(published)\n"
        "visible_sha = published[-1] if published else sha\n"
        "print(json.dumps([{'databaseId': 41, 'headSha': os.environ.get('FEATURE_SIGNAL_SHA', visible_sha),\n"
        "                  'url': 'https://github.com/sandboxcom/gludd/actions/runs/41',\n"
        "                  'status': 'queued', 'conclusion': '', 'event': 'workflow_dispatch'}] if visible else []))\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    fixture.environment.update(
        {
            "FEATURE_BASELINE_SHA": baseline,
            "FEATURE_BRANCH": "feature/owned-source",
            "GLUDD_GHA_SIGNAL_STATE_DIR": str(fixture.root.parent / "owned-signal-state"),
            "GLUDD_CI_STATE_FILE": str(fixture.root.parent / "owned-check-state.json"),
            "GLUDD_CI_HISTORY_FILE": str(fixture.root.parent / "owned-history.json"),
            "GLUDD_CI_RESTART_COUNT_FILE": str(fixture.root.parent / "owned-restart-count"),
            "GLUDD_PUSH_STATE_FILE": str(fixture.root.parent / "owned-push-state.json"),
        }
    )
    fixture.calls.unlink(missing_ok=True)
    return fixture


def _feature_arguments(fixture: DevelopmentFixture, **changes: str) -> list[str]:
    values = {
        "PUBLISH_PURPOSE": "feature-ci",
        BRANCH_INPUT: fixture.environment["FEATURE_BRANCH"],
        SHA_INPUT: fixture.sha,
        "PUBLISH_BASELINE_REF": "master",
        "PUBLISH_BASELINE_SHA": fixture.environment["FEATURE_BASELINE_SHA"],
        "PYTHON": shlex.quote(sys.executable),
        "DISCOVERY_POLLS": "1",
        "CONFIRM_POLLS": "1",
        "POLL_INTERVAL": "0",
    }
    values.update(changes)
    return [key + "=" + value for key, value in values.items()]


def _feature_remote_sha(fixture: DevelopmentFixture) -> str:
    return fixture.git("rev-parse", "refs/heads/" + fixture.environment["FEATURE_BRANCH"], bare=True).stdout.strip()


@pytest.mark.parametrize(
    "baseline_ref",
    (
        "master",
        "release-candidate/v012-git-mutex-20261022",
        "release-candidate/owned/nested",
    ),
)
@pytest.mark.parametrize("branch", ("feature/owned-source", "diagnostic/owned-source"))
def test_feature_ci_uses_native_hooks_saved_record_verify_and_existing_signal(
    feature_repo: DevelopmentFixture,
    branch: str,
    baseline_ref: str,
) -> None:
    fixture = feature_repo
    baseline = fixture.environment["FEATURE_BASELINE_SHA"]
    if baseline_ref != "master":
        fixture.git("update-ref", "refs/heads/" + baseline_ref, baseline, bare=True)
    assert fixture.git("rev-parse", "refs/heads/" + baseline_ref, bare=True).stdout.strip() == baseline
    if branch != fixture.environment["FEATURE_BRANCH"]:
        fixture.git("branch", "-m", branch)
        fixture.environment["FEATURE_BRANCH"] = branch
    result = fixture.invoke(
        "git-push-sandboxcom",
        *_feature_arguments(fixture, PUBLISH_BASELINE_REF=baseline_ref),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _feature_remote_sha(fixture) == fixture.sha
    assert fixture.git("rev-parse", "--symbolic-full-name", "@{u}").stdout.strip() == (
        "refs/remotes/sandboxcom/" + branch
    )
    assert {row["args"][0] for row in fixture.rows("guard")} >= set(ORIGINAL_GUARDS)
    assert fixture.rows("runtime") == []
    assert len(fixture.rows("hook")) == len(fixture.rows("transport")) == len(fixture.rows("dispatch")) == 1
    assert fixture.rows("transport")[0]["args"] == ["push", "-u", "sandboxcom", fixture.sha + ":refs/heads/" + branch]
    history = json.loads(Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).read_text(encoding="utf-8"))
    assert history["last_push_sha"] == fixture.sha
    assert "VERIFIED " + branch + "@" + fixture.sha in result.stdout
    assert "GHA_RUN_URL=https://github.com/sandboxcom/gludd/actions/runs/41" in result.stdout
    assert "purpose=feature-ci" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert all(row["args"][row["args"].index("--commit") + 1] == fixture.sha for row in fixture.rows("lookup"))
    assert fixture.git("rev-parse", "refs/heads/" + baseline_ref, bare=True).stdout.strip() == baseline
    assert ["ls-remote", "--exit-code", "--heads", "sandboxcom", "refs/heads/" + baseline_ref] in [
        row["args"] for row in fixture.rows("git")
    ]
    kinds = [row["kind"] for row in fixture.rows()]
    assert kinds.index("transport") < kinds.index("hook") < kinds.index("dispatch")
    assert not (fixture.root.parent / "forbidden-external-command").exists()


@pytest.mark.parametrize(
    "baseline_ref",
    (
        "",
        "release-candidate",
        "release-candidate/",
        "release-candidates/owned",
        "release-candidate-lookalike/owned",
        "refs/heads/release-candidate/owned",
        "refs/tags/v0.1.2",
        "agent-v012-owned",
        "feature/owned-source",
        "release-candidate/owned..branch",
        "release-candidate/owned.lock",
        "release-candidate/owned.",
        "release-candidate/owned//nested",
    ),
)
def test_feature_ci_refuses_malformed_or_unselected_candidate_baseline(
    feature_repo: DevelopmentFixture,
    baseline_ref: str,
) -> None:
    fixture = feature_repo
    invalid_native_ref = (
        fixture.git(
            "check-ref-format",
            "refs/heads/" + baseline_ref,
            check=False,
        ).returncode
        != 0
    )
    result = fixture.invoke(
        "git-push-sandboxcom",
        *_feature_arguments(fixture, PUBLISH_BASELINE_REF=baseline_ref),
    )
    assert result.returncode != 0
    assert "phase=diagnostic-preflight" in result.stderr
    if baseline_ref.startswith("release-candidate/") and baseline_ref != "release-candidate/" and invalid_native_ref:
        assert "invalid diagnostic baseline branch" in result.stderr
    assert (
        fixture.git("rev-parse", "refs/heads/master", bare=True).stdout.strip()
        == (fixture.environment["FEATURE_BASELINE_SHA"])
    )
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")
    assert not Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).exists()


@pytest.mark.parametrize("branch", ("development", "master", "main", "release-candidate/owned", "other/owned"))
def test_feature_ci_refuses_protected_or_unselected_branch_before_transport(
    feature_repo: DevelopmentFixture,
    branch: str,
) -> None:
    fixture = feature_repo
    if branch == "development":
        fixture.git("branch", "-m", "development", "owned-original-development")
    fixture.git("branch", "-m", branch)
    fixture.environment["FEATURE_BRANCH"] = branch
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    assert "phase=diagnostic" in result.stderr
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")
    assert not Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).exists()


@pytest.mark.parametrize(
    "changes",
    (
        {BRANCH_INPUT: ""},
        {SHA_INPUT: ""},
        {"PUBLISH_BASELINE_REF": ""},
        {"PUBLISH_BASELINE_SHA": ""},
        {"PUBLISH_BASELINE_SHA": "a" * 39},
        {"PUBLISH_BASELINE_SHA": "f" * 40},
        {"PUBLISH_BASELINE_REF": "feature/owned-source"},
        {"FORCE": "1"},
        {"GLUDD_FORCE_PUSH": "1"},
        {"PUBLISH_PURPOSE": "other"},
    ),
)
def test_feature_ci_refuses_missing_or_unsafe_identity_and_baseline(
    feature_repo: DevelopmentFixture,
    changes: dict[str, str],
) -> None:
    fixture = feature_repo
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture, **changes))
    assert result.returncode != 0
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")
    assert not Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).exists()


def test_feature_ci_refuses_changed_workflow_bytes_from_actual_published_baseline(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    workflow = fixture.root / ".github/workflows/build.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\n# owned changed workflow\n", encoding="utf-8")
    fixture.git("add", "-f", ".github/workflows/build.yml")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned workflow difference")
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    assert "workflow" in result.stderr.lower()
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


def test_feature_ci_canonical_default_still_stops_at_failed_runtime(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    fixture.environment.update({"DEVELOPMENT_FAIL_PHASE": "runtime", "DEVELOPMENT_FAIL_STATUS": "83"})
    result = fixture.invoke("git-push-sandboxcom", "PYTHON=" + shlex.quote(sys.executable))
    assert result.returncode != 0
    assert "native-fixture-runtime-83" in result.stderr
    assert len(fixture.rows("runtime")) == 1
    _not_started(fixture, "transport", "hook", "dispatch")


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_recorder_input_is_literal_at_actual_native_owner(
    development_repo: DevelopmentFixture,
    origin: str,
    payload_kind: str,
) -> None:
    fixture = development_repo
    history, restart = _install_native_recorder(fixture)
    marker = fixture.root / "owned unexpected record evaluation"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_kind == "make" else chr(96) + command + chr(96)
    environment = dict(fixture.environment)
    arguments = ["PYTHON=" + shlex.quote(sys.executable)]
    if origin == "argv":
        arguments.append("PUSH_VERDICT_SHA=" + payload)
    else:
        environment["PUSH_VERDICT_SHA"] = payload
    control = fixture.root.parent / "owned-unguarded-record-input.mk"
    control.write_text(
        "literal-control:\n\t@printf '%s\\n' \"$(PUSH_VERDICT_SHA)\"\n",
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control",
        *arguments,
        makefile=control,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), "the exact recorder payload/variable/origin must execute unguarded"
    marker.unlink()
    result = fixture.invoke(
        "_record-push-verdict",
        *arguments,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert not marker.exists()
    assert result.returncode != 0
    assert "explicit SHA must be full40hex" in result.stderr
    assert not history.exists() and not restart.exists()


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("variable", ("PUBLISH_PURPOSE", "PUBLISH_BASELINE_REF", "PUBLISH_BASELINE_SHA"))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_feature_inputs_remain_literal_with_same_origin_native_witness(
    feature_repo: DevelopmentFixture,
    origin: str,
    variable: str,
    payload_kind: str,
) -> None:
    fixture = feature_repo
    marker = fixture.root / "owned unexpected feature evaluation"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_kind == "make" else chr(96) + command + chr(96)
    environment = dict(fixture.environment)
    arguments = [argument for argument in _feature_arguments(fixture) if not argument.startswith(variable + "=")]
    if origin == "argv":
        arguments.append(variable + "=" + payload)
    else:
        environment[variable] = payload
    control = fixture.root.parent / "owned-unguarded-feature-input.mk"
    control.write_text(
        "literal-control:\n\t@printf '%s\\n' \"$(" + variable + ')"\n',
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control",
        *arguments,
        makefile=control,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), "same payload/variable/origin must execute in unguarded native Make"
    marker.unlink()
    startup = fixture.invoke(
        "help",
        *arguments,
        makefile=ROOT / "Makefile",
        cwd=ROOT,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert startup.returncode == 0, startup.stdout + startup.stderr
    assert not marker.exists()
    result = fixture.invoke(
        "git-push-sandboxcom",
        *arguments,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert result.returncode != 0
    assert not marker.exists()
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


@pytest.mark.parametrize("goal", ("git-push-sandboxcom", "ci-trigger-committed-head", "ci-trigger"))
@pytest.mark.parametrize("mode", ("-n", "-q", "-t"))
@pytest.mark.parametrize("early_environment", (False, True))
def test_feature_native_inspection_runs_no_auth_git_hook_dispatch_or_touch(
    feature_repo: DevelopmentFixture,
    goal: str,
    mode: str,
    early_environment: bool,
) -> None:
    fixture = feature_repo
    result = fixture.invoke(
        goal,
        *_feature_arguments(fixture),
        "REF=" + fixture.environment["FEATURE_BRANCH"],
        mode=mode,
        early_environment=early_environment,
    )
    assert result.returncode == (1 if mode == "-q" else 0), result.stdout + result.stderr
    assert fixture.rows() == []
    assert not (fixture.root / goal).exists()
    assert not Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).exists()
    assert not Path(fixture.environment["GLUDD_GHA_SIGNAL_STATE_DIR"]).exists()


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
def test_feature_purpose_preserves_native_cli_and_environment_precedence(
    feature_repo: DevelopmentFixture,
    origin: str,
) -> None:
    fixture = feature_repo
    environment = dict(fixture.environment)
    arguments = _feature_arguments(fixture)
    if origin != "argv":
        environment["PUBLISH_PURPOSE"] = "feature-ci"
        arguments = [argument for argument in arguments if not argument.startswith("PUBLISH_PURPOSE=")]
    else:
        environment["PUBLISH_PURPOSE"] = "canonical"
    result = fixture.invoke(
        "git-push-sandboxcom",
        *arguments,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(fixture.rows("dispatch")) == 1
    assert fixture.rows("runtime") == []


@pytest.mark.parametrize("state", ("dirty", "detached"))
def test_feature_preflight_retains_native_clean_and_attached_head_guards(
    feature_repo: DevelopmentFixture,
    state: str,
) -> None:
    fixture = feature_repo
    if state == "dirty":
        (fixture.root / "tracked.txt").write_text("owned dirty source\n", encoding="utf-8")
    else:
        fixture.git("checkout", "--quiet", "--detach")
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


def test_feature_baseline_remote_object_must_exist_locally_before_transport(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    base = fixture.environment["FEATURE_BASELINE_SHA"]
    tree = fixture.git("rev-parse", base + "^{tree}", bare=True).stdout.strip()
    remote_only = fixture.git(
        "-c",
        "user.name=Owned remote",
        "-c",
        "user.email=remote@fixture.invalid",
        "commit-tree",
        tree,
        "-p",
        base,
        "-m",
        "owned remote-only baseline",
        bare=True,
    ).stdout.strip()
    fixture.git("update-ref", "refs/heads/master", remote_only, bare=True)
    assert fixture.git("cat-file", "-e", remote_only, check=False).returncode != 0
    result = fixture.invoke(
        "git-push-sandboxcom",
        *_feature_arguments(fixture, PUBLISH_BASELINE_SHA=remote_only),
    )
    assert result.returncode != 0
    assert "phase=diagnostic-preflight native_status=128" in result.stderr
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


def test_feature_baseline_must_be_actual_source_ancestor(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    branch = fixture.environment["FEATURE_BRANCH"]
    fixture.git("checkout", "--quiet", "--orphan", "owned-unrelated-baseline")
    fixture.git("read-tree", "--empty")
    (fixture.root / "unrelated.txt").write_text("owned unrelated source\n", encoding="utf-8")
    fixture.git("add", "-f", "unrelated.txt")
    fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned unrelated")
    unrelated = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.git("fetch", "--quiet", "--no-tags", str(fixture.root), "owned-unrelated-baseline", bare=True)
    fixture.git("update-ref", "refs/heads/master", unrelated, bare=True)
    fixture.git("checkout", "--quiet", branch)
    result = fixture.invoke(
        "git-push-sandboxcom",
        *_feature_arguments(fixture, PUBLISH_BASELINE_SHA=unrelated),
    )
    assert result.returncode != 0
    assert "ancestor" in result.stderr
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


@pytest.mark.parametrize("failure", ("hook", "transport", "verification", "dispatch"))
def test_feature_failure_never_claims_ci_or_retries_applied_transport(
    feature_repo: DevelopmentFixture,
    failure: str,
) -> None:
    fixture = feature_repo
    if failure == "hook":
        fixture.environment.update({"DEVELOPMENT_FAIL_PHASE": "hook", "DEVELOPMENT_FAIL_STATUS": "84"})
    elif failure == "transport":
        fixture.environment["DEVELOPMENT_PUSH_STATUS"] = "85"
    elif failure == "verification":
        adapter = Path(fixture.environment["PATH"].split(os.pathsep)[0]) / "git"
        source = adapter.read_text(encoding="utf-8")
        needle = "if args[0] == 'ls-remote' and os.environ.get('DEVELOPMENT_VERIFY_STATUS'):"
        source = source.replace(
            needle, "if args[0] == 'ls-remote' and '--exit-code' in args and 'refs/heads/feature/owned-source' in args:"
        )
        source = source.replace("code = int(os.environ['DEVELOPMENT_VERIFY_STATUS'])", "code = 86")
        adapter.write_text(source, encoding="utf-8")
    else:
        fixture.environment["FEATURE_GH_STATUS"] = "87"
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    assert "outcome=diagnostic-signaled" not in result.stderr
    assert len(fixture.rows("transport")) <= 1
    assert fixture.rows("runtime") == []
    if failure != "dispatch":
        assert fixture.rows("dispatch") == []
    if failure == "transport":
        assert "native_status=85" in result.stderr
        assert not Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).exists()


def test_feature_transport_head_shift_records_saved_sha_but_refuses_different_dispatch(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    fixture.environment["DEVELOPMENT_MUTATE_TRANSPORT"] = "1"
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    assert _feature_remote_sha(fixture) == fixture.sha
    history = json.loads(Path(fixture.environment["GLUDD_CI_HISTORY_FILE"]).read_text(encoding="utf-8"))
    assert history["last_push_sha"] == fixture.sha
    assert "phase=diagnostic-signal" in result.stderr
    assert fixture.rows("dispatch") == []
    assert "outcome=diagnostic-signaled" not in result.stderr


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("variable", ("REF", "REMOTE", "REPO", "WORKFLOW"))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_existing_signal_inputs_are_literal_at_the_native_diagnostic_caller(
    feature_repo: DevelopmentFixture,
    origin: str,
    variable: str,
    payload_kind: str,
) -> None:
    fixture = feature_repo
    marker = fixture.root / "owned unexpected signal evaluation"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_kind == "make" else chr(96) + command + chr(96)
    values = {
        "REF": fixture.environment["FEATURE_BRANCH"],
        "REMOTE": "sandboxcom",
        "REPO": "sandboxcom/gludd",
        "WORKFLOW": "Build and Release",
    }
    environment = dict(fixture.environment)
    arguments = _feature_arguments(fixture)
    arguments.extend(key + "=" + value for key, value in values.items() if key != variable)
    if origin == "argv":
        arguments.append(variable + "=" + payload)
    else:
        environment[variable] = payload
    control = fixture.root.parent / "owned-unguarded-signal-input.mk"
    control.write_text(
        "literal-control:\n\t@printf '%s\\n' \"$(" + variable + ')"\n',
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control",
        *arguments,
        makefile=control,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), "same existing signal payload/variable/origin must execute unguarded"
    marker.unlink()
    result = fixture.invoke(
        "ci-trigger-committed-head",
        *arguments,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert result.returncode != 0
    assert not marker.exists()
    _not_started(fixture, "auth", "runtime", "transport", "hook", "lookup", "dispatch")


def test_diagnostic_signal_refuses_foreign_remote_before_native_lookup(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    result = fixture.invoke(
        "ci-trigger-committed-head",
        *_feature_arguments(fixture),
        "REF=" + fixture.environment["FEATURE_BRANCH"],
        "REMOTE=" + str(fixture.remote),
    )
    assert result.returncode != 0
    assert not any(row["args"][0] == "ls-remote" for row in fixture.rows("git"))
    _not_started(fixture, "auth", "runtime", "transport", "lookup", "dispatch")


@pytest.mark.parametrize("guarded", (False, True))
def test_native_dispatch_boundary_keeps_saved_sha_when_both_git_heads_move(
    feature_repo: DevelopmentFixture,
    guarded: bool,
) -> None:
    fixture = feature_repo
    if not guarded:
        copied = fixture.root / "scripts/ci_signal_exact_sha.py"
        source = copied.read_text(encoding="utf-8")
        guard = (
            '            if expected_branch or expected_sha or purpose == "feature-ci":\n'
            "                _guard_signal_source(\n"
            "                    ref=ref, remote=remote, repo=repo, workflow=workflow, purpose=purpose,\n"
            "                    expected_branch=expected_branch, expected_sha=expected_sha,\n"
            "                    baseline_ref=baseline_ref, baseline_sha=baseline_sha, run=run, cwd=cwd,\n"
            "                )\n"
        )
        assert source.count(guard) == 1
        copied.write_text(source.replace(guard, ""), encoding="utf-8")
    fixture.environment["FEATURE_MUTATE_LOOKUP"] = "1"
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    shifted = fixture.git("rev-parse", "HEAD").stdout.strip()
    assert shifted != fixture.sha
    assert _feature_remote_sha(fixture) == shifted
    assert len(fixture.rows("dispatch")) == (0 if guarded else 1)
    assert "outcome=diagnostic-signaled" not in result.stderr
    assert len(fixture.rows("transport")) == 1


def test_native_signal_alias_reuses_the_same_exact_sha_run_without_republishing(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    first = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert first.returncode == 0, first.stdout + first.stderr
    again = fixture.invoke(
        "ci-trigger",
        *_feature_arguments(fixture),
        "REF=" + fixture.environment["FEATURE_BRANCH"],
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert "GHA-SIGNAL-EXISTING sha=" + fixture.sha in again.stdout
    assert len(fixture.rows("dispatch")) == len(fixture.rows("transport")) == 1
    assert fixture.rows("runtime") == []


def test_native_signal_rejects_nonmatching_confirmation_without_false_ci_success(
    feature_repo: DevelopmentFixture,
) -> None:
    fixture = feature_repo
    fixture.environment["FEATURE_SIGNAL_SHA"] = "c" * 40
    result = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert result.returncode != 0
    assert _feature_remote_sha(fixture) == fixture.sha
    assert len(fixture.rows("dispatch")) == 1
    assert "not visible after dispatch confirmation" in result.stderr
    assert "outcome=diagnostic-signaled" not in result.stderr
    assert list(Path(fixture.environment["GLUDD_GHA_SIGNAL_STATE_DIR"]).glob("*.json"))


@pytest.mark.parametrize("baseline_ref", ("master", "release-candidate/v012-git-mutex-20261022"))
def test_feature_ci_refuses_reverted_workflow_history_with_equal_final_bytes(
    feature_repo: DevelopmentFixture,
    baseline_ref: str,
) -> None:
    fixture = feature_repo
    if baseline_ref != "master":
        fixture.git(
            "update-ref",
            "refs/heads/" + baseline_ref,
            fixture.environment["FEATURE_BASELINE_SHA"],
            bare=True,
        )
    path = fixture.root / ".github/workflows/build.yml"
    original = path.read_text(encoding="utf-8")
    for index, text in enumerate((original + "\n# owned transient workflow\n", original)):
        path.write_text(text, encoding="utf-8")
        fixture.git("add", "-f", ".github/workflows/build.yml")
        fixture.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "owned workflow " + str(index))
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    baseline = fixture.environment["FEATURE_BASELINE_SHA"]
    assert fixture.git("diff", "--quiet", baseline, fixture.sha, "--", ".github/workflows").returncode == 0
    changed = fixture.git(
        "log",
        "--format=%H",
        "--full-history",
        baseline + ".." + fixture.sha,
        "--",
        ".github/workflows",
    ).stdout.splitlines()
    assert len(changed) == 2
    result = fixture.invoke(
        "git-push-sandboxcom",
        *_feature_arguments(fixture, PUBLISH_BASELINE_REF=baseline_ref),
    )
    assert result.returncode != 0
    assert "workflow history changed" in result.stderr.lower()
    _not_started(fixture, "runtime", "transport", "hook", "dispatch")


def test_native_porcelain_keeps_staged_unstaged_and_untracked_counts(
    development_repo: DevelopmentFixture,
) -> None:
    fixture = development_repo
    (fixture.root / "tracked.txt").write_text("owned unstaged edit\n", encoding="utf-8")
    (fixture.root / "owned-staged.txt").write_text("owned staged source\n", encoding="utf-8")
    fixture.git("add", "-f", "owned-staged.txt")
    (fixture.root / "owned-untracked.txt").write_text("owned untracked source\n", encoding="utf-8")
    exclude = fixture.root / ".git/info/exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "\n!owned-untracked.txt\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_remote_head_guard.py"),
            "--json",
            "--ref",
            "development",
            "--remote",
            "sandboxcom",
        ],
        cwd=fixture.root,
        env=fixture.environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert (payload["dirty_count"], payload["staged_count"], payload["untracked_count"]) == (3, 1, 1)
    assert payload["is_clean"] is False
    assert "1 staged change(s) would not be present in the remote CI run" in payload["errors"]
    assert "3 local dirty path(s) would make local tests differ from remote CI" in payload["errors"]
    assert fixture.git("diff", "--cached", "--name-only").stdout.strip() == "owned-staged.txt"
    _not_started(fixture, "auth", "runtime", "transport", "hook", "dispatch")


def test_native_raw_sha_push_witness_retains_missing_upstream(
    development_repo: DevelopmentFixture,
) -> None:
    fixture = development_repo
    fixture.git("branch", "--unset-upstream")
    result = fixture.git("push", "-u", str(fixture.remote), fixture.sha + ":refs/heads/development")
    assert result.returncode == 0
    assert fixture.remote_sha() == fixture.sha
    upstream = fixture.git("rev-parse", "--symbolic-full-name", "@{u}", check=False)
    assert upstream.returncode == 128
    assert "no upstream configured" in upstream.stderr
    assert fixture.git("config", "--get", "branch.development.remote", check=False).returncode == 1


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
def test_transport_publishes_saved_candidate_when_native_boundary_moves_head(
    development_repo: DevelopmentFixture,
    goal: str,
) -> None:
    fixture = development_repo
    fixture.environment["DEVELOPMENT_MUTATE_TRANSPORT"] = "1"
    result = fixture.invoke(goal)
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() != fixture.sha
    assert fixture.remote_sha() == fixture.sha
    assert f"VERIFIED development@{fixture.sha}" in result.stdout


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
def test_native_recorder_keeps_pushed_sha_when_transport_moves_head(
    development_repo: DevelopmentFixture,
    goal: str,
) -> None:
    """Exercise the actual recorder target and Python CLI after native Git moves."""
    fixture = development_repo
    history, restart = _install_native_recorder(fixture)
    fixture.environment["DEVELOPMENT_MUTATE_TRANSPORT"] = "1"
    result = fixture.invoke(goal, "PYTHON=" + shlex.quote(sys.executable))
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() != fixture.sha
    assert fixture.remote_sha() == fixture.sha
    assert json.loads(history.read_text(encoding="utf-8"))["last_push_sha"] == fixture.sha
    assert restart.read_text(encoding="utf-8") == "1"
    assert f"VERIFIED development@{fixture.sha}" in result.stdout


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
def test_normal_first_publication_preserves_native_branch_tracking(
    development_repo: DevelopmentFixture,
    goal: str,
) -> None:
    fixture = development_repo
    fixture.git("branch", "--unset-upstream")
    result = fixture.invoke(goal)
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.remote_sha() == fixture.sha
    assert fixture.git("config", "--get", "branch.development.remote").stdout.strip() == "sandboxcom"
    assert fixture.git("config", "--get", "branch.development.merge").stdout.strip() == "refs/heads/development"
    assert fixture.git("rev-parse", "--symbolic-full-name", "@{u}").stdout.strip() == (
        "refs/remotes/sandboxcom/development"
    )


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
def test_postpush_tracking_failure_retains_applied_transport_and_native_status(
    development_repo: DevelopmentFixture,
    goal: str,
) -> None:
    fixture = development_repo
    fixture.environment["DEVELOPMENT_GIT_FAULT"] = "tracking"
    result = fixture.invoke(goal)
    assert result.returncode != 0
    assert "native-fixture-tracking-76" in result.stderr
    assert "phase=tracking native_status=76" in result.stderr
    assert "transport_may_have_applied=true" in result.stderr
    assert "reconcile" in result.stderr
    assert fixture.remote_sha() == fixture.sha
    _not_started(fixture, "verdict")
    assert not any(row["args"][0] == "ls-remote" for row in fixture.rows("git"))
    assert "outcome=published" not in result.stderr


def _not_started(fixture: DevelopmentFixture, *kinds: str) -> None:
    for kind in kinds:
        assert fixture.rows(kind) == [], fixture.rows()


@pytest.mark.parametrize("transport", ("https", "ssh"))
def test_development_uses_normal_guarded_transport_and_saved_full_sha(
    development_repo: DevelopmentFixture,
    transport: str,
) -> None:
    fixture = development_repo
    if transport == "https":
        fixture.key.unlink()
    else:
        fixture.git("remote", "set-url", "sandboxcom", "git@github.com:sandboxcom/gludd.git")
    result = fixture.invoke()
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.remote_sha() == fixture.sha
    assert {row["args"][0] for row in fixture.rows("guard")} >= set(ORIGINAL_GUARDS)
    assert len(fixture.rows("runtime")) == len(fixture.rows("transport")) == 1
    assert len(fixture.rows("hook")) == len(fixture.rows("verdict")) == 1
    assert fixture.rows("transport")[0]["args"] == [
        "push",
        "-u",
        "sandboxcom",
        fixture.sha + ":refs/heads/development",
    ]
    assert all(
        row["branch"] == "development"
        for row in fixture.rows("guard")
        if row["args"] == ["ci-busy-check"] and row["branch"]
    )
    kinds = [row["kind"] for row in fixture.rows()]
    assert kinds.index("runtime") < kinds.index("transport") < kinds.index("hook") < kinds.index("verdict")
    queries = [row["args"] for row in fixture.rows("git") if row["args"][0] == "ls-remote"]
    assert queries == [["ls-remote", "--exit-code", "--heads", "sandboxcom", "refs/heads/development"]]
    assert f"VERIFIED development@{fixture.sha}" in result.stdout
    assert f"branch=development sha={fixture.sha}" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert bool(fixture.rows("auth")) is (transport == "https")
    assert not (fixture.root.parent / "forbidden-external-command").exists()


@pytest.mark.parametrize("state", ("other-branch", "detached"))
def test_development_refuses_an_unselected_candidate_before_publication(
    development_repo: DevelopmentFixture,
    state: str,
) -> None:
    fixture = development_repo
    fixture.git("checkout", "--quiet", *(["-b", "owned-other"] if state == "other-branch" else ["--detach"]))
    result = fixture.invoke()
    assert result.returncode != 0
    assert "target=development-push outcome=refused phase=candidate" in result.stderr
    _not_started(fixture, "auth", "runtime", "transport", "verdict")


@pytest.mark.parametrize(("fault", "native_status"), (("branch", 74), ("head", 75)))
def test_development_retains_native_candidate_read_failure(
    development_repo: DevelopmentFixture,
    fault: str,
    native_status: int,
) -> None:
    fixture = development_repo
    fixture.environment["DEVELOPMENT_GIT_FAULT"] = fault
    result = fixture.invoke()
    assert result.returncode != 0
    assert f"native-fixture-{fault}-{native_status}" in result.stderr
    _not_started(fixture, "auth", "runtime", "transport", "verdict")


@pytest.mark.parametrize(
    ("branch", "sha"),
    (
        ("development", ""),
        ("", "a" * 40),
        ("development", "bad"),
        ("development", "g" * 40),
        ("bad branch", "a" * 40),
    ),
)
def test_publisher_refuses_malformed_expected_pair_before_auth_or_runtime(
    development_repo: DevelopmentFixture,
    branch: str,
    sha: str,
) -> None:
    fixture = development_repo
    result = fixture.invoke("git-push-sandboxcom", f"{BRANCH_INPUT}={branch}", f"{SHA_INPUT}={sha}")
    assert result.returncode != 0
    assert "phase=expected-input" in result.stderr
    _not_started(fixture, "auth", "runtime", "transport", "verdict")


@pytest.mark.parametrize("expected", ("absent", "matching", "upper-case"))
def test_normal_publisher_keeps_optional_pair_and_unrelated_branch_sha_semantics(
    development_repo: DevelopmentFixture,
    expected: str,
) -> None:
    fixture = development_repo
    pair = (
        ()
        if expected == "absent"
        else (
            f"{BRANCH_INPUT}=development",
            f"{SHA_INPUT}={fixture.sha.upper() if expected == 'upper-case' else fixture.sha}",
        )
    )
    result = fixture.invoke("git-push-sandboxcom", "BRANCH=unrelated", "SHA=bad", *pair)
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.remote_sha() == fixture.sha
    assert f"VERIFIED development@{fixture.sha}" in result.stdout
    assert len(fixture.rows("verdict")) == 1


@pytest.mark.parametrize("mutation", ("head", "branch"))
def test_development_pins_candidate_across_normal_publication_prerequisites(
    development_repo: DevelopmentFixture,
    mutation: str,
) -> None:
    fixture = development_repo
    fixture.environment.update(
        {
            "DEVELOPMENT_MUTATE_PHASE": "guard:_pull-before-push-guard",
            "DEVELOPMENT_MUTATION": mutation,
        }
    )
    result = fixture.invoke()
    assert result.returncode != 0
    assert "phase=expected-identity" in result.stderr
    _not_started(fixture, "auth", "runtime", "transport", "verdict")


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
@pytest.mark.parametrize("mutation", ("head", "branch"))
def test_publication_rechecks_actual_candidate_after_runtime(
    development_repo: DevelopmentFixture,
    goal: str,
    mutation: str,
) -> None:
    fixture = development_repo
    fixture.environment.update({"DEVELOPMENT_MUTATE_PHASE": "runtime", "DEVELOPMENT_MUTATION": mutation})
    result = fixture.invoke(goal)
    assert result.returncode != 0
    assert len(fixture.rows("runtime")) == 1
    assert "phase=identity" in result.stderr
    _not_started(fixture, "transport", "verdict")


@pytest.mark.parametrize("guard", (*ORIGINAL_GUARDS, "ci-busy-check"))
def test_development_retains_each_existing_normal_guard_refusal(
    development_repo: DevelopmentFixture,
    guard: str,
) -> None:
    fixture = development_repo
    fixture.environment.update({"DEVELOPMENT_FAIL_PHASE": "guard:" + guard, "DEVELOPMENT_FAIL_STATUS": "83"})
    result = fixture.invoke()
    assert result.returncode != 0
    assert f"native-fixture-guard:{guard}-83" in result.stderr
    _not_started(fixture, "runtime", "transport", "verdict")


@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
@pytest.mark.parametrize(
    ("phase", "status", "forbidden"),
    (
        ("auth", 69, ("runtime", "transport", "verdict")),
        ("runtime", 77, ("transport", "verdict")),
        ("transport", 73, ("verdict",)),
        ("verdict", 78, ()),
        ("verify", 72, ()),
    ),
)
def test_publication_preserves_native_failure_order_and_partial_write_guidance(
    development_repo: DevelopmentFixture,
    goal: str,
    phase: str,
    status: int,
    forbidden: tuple[str, ...],
) -> None:
    fixture = development_repo
    if phase in {"auth", "transport", "verify"}:
        key = {
            "auth": "DEVELOPMENT_AUTH_STATUS",
            "transport": "DEVELOPMENT_PUSH_STATUS",
            "verify": "DEVELOPMENT_VERIFY_STATUS",
        }[phase]
        fixture.environment[key] = str(status)
    else:
        fixture.environment.update({"DEVELOPMENT_FAIL_PHASE": phase, "DEVELOPMENT_FAIL_STATUS": str(status)})
    result = fixture.invoke(goal)
    assert result.returncode != 0
    assert f"native-fixture-{phase}-{status}" in result.stderr
    _not_started(fixture, *forbidden)
    if phase in {"verdict", "verify"}:
        assert fixture.remote_sha() == fixture.sha
        assert "may" in result.stderr.lower() and "reconcile" in result.stderr.lower()
    assert "outcome=published" not in result.stderr
    assert "VERIFIED " not in result.stdout
    if phase == "transport":
        assert f"native_status={status}" in result.stderr
    elif phase in {"runtime", "verdict"}:
        assert "native_status=2" in result.stderr


@pytest.mark.parametrize(("fault", "status"), (("post-branch", 74), ("post-head", 75)))
def test_publisher_retains_postruntime_native_identity_read_failure(
    development_repo: DevelopmentFixture,
    fault: str,
    status: int,
) -> None:
    fixture = development_repo
    fixture.environment["DEVELOPMENT_GIT_FAULT"] = fault
    result = fixture.invoke()
    assert result.returncode != 0
    assert f"Error {status}" in result.stderr
    _not_started(fixture, "transport", "verdict")


def test_postpublication_verification_uses_saved_sha_after_local_head_moves(
    development_repo: DevelopmentFixture,
) -> None:
    fixture = development_repo
    fixture.environment.update({"DEVELOPMENT_MUTATE_PHASE": "verdict", "DEVELOPMENT_MUTATION": "head"})
    result = fixture.invoke()
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.git("rev-parse", "HEAD").stdout.strip() != fixture.sha
    assert fixture.remote_sha() == fixture.sha
    assert f"VERIFIED development@{fixture.sha}" in result.stdout
    assert f"branch=development sha={fixture.sha}" in result.stderr


@pytest.mark.parametrize("force", ("", "0", "1"))
def test_development_delegation_preserves_original_force_carriers_and_hooks(
    development_repo: DevelopmentFixture,
    force: str,
) -> None:
    fixture = development_repo
    result = fixture.invoke("development-push", f"FORCE={force}", "GLUDD_FORCE_PUSH=1")
    assert result.returncode == 0, result.stdout + result.stderr
    for row in fixture.rows("guard") + fixture.rows("runtime") + fixture.rows("verdict"):
        assert row["force"] == force and row["force_push"] == "1"
        assert row["carrier"] == "retained-unrelated-carrier"
    assert len(fixture.rows("hook")) == 1
    assert fixture.rows("transport")[0]["args"] == [
        "push",
        "-u",
        "sandboxcom",
        fixture.sha + ":refs/heads/development",
    ]


@pytest.mark.parametrize("early_environment", (False, True))
@pytest.mark.parametrize("goal", ("development-push", "git-push-sandboxcom"))
@pytest.mark.parametrize(
    ("mode", "status"),
    (
        ("-n", 0),
        ("-q", 1),
        ("-t", 0),
        ("--just-print", 0),
        ("--question", 1),
        ("--touch", 0),
    ),
)
def test_native_inspection_starts_no_auth_runtime_publication_or_target_touch(
    development_repo: DevelopmentFixture,
    goal: str,
    mode: str,
    status: int,
    early_environment: bool,
) -> None:
    fixture = development_repo
    old = fixture.remote_sha()
    result = fixture.invoke(goal, mode=mode, early_environment=early_environment)
    assert result.returncode == status, result.stdout + result.stderr
    assert fixture.rows() == []
    assert fixture.remote_sha() == old
    assert not (fixture.root / goal).exists()
    assert not (fixture.root.parent / "forbidden-external-command").exists()


@pytest.mark.parametrize(
    ("arguments", "expected_mode", "native_status"),
    (
        (("-q",), "query", 1),
        (("-n",), "dry-run", 0),
        (("-t",), "touch", 0),
        (("-enq",), "query", 1),
        (("-ent",), "dry-run", 0),
        (("--question",), "query", 1),
        (("--just-print",), "dry-run", 0),
        (("--touch",), "touch", 0),
    ),
)
def test_native_early_environment_modes_classify_actual_gnu_makeflags(
    development_repo: DevelopmentFixture,
    arguments: tuple[str, ...],
    expected_mode: str,
    native_status: int,
) -> None:
    fixture = development_repo
    control = fixture.root.parent / "native-inspection-mode-control.mk"
    control.write_text(
        _inspection_inputs(RECOVERY.read_text(encoding="utf-8"))
        + "$(info owned-native-mode=$(_publication-native-mode))\n"
        + ".PHONY: mode-control\nmode-control:\n\t@true\n",
        encoding="utf-8",
    )
    result = fixture.invoke(
        "mode-control",
        *arguments,
        early_environment=True,
        makefile=control,
    )
    assert result.returncode == native_status, result.stdout + result.stderr
    assert "owned-native-mode=" + expected_mode in result.stdout
    assert fixture.rows() == []
    assert not (fixture.root / "mode-control").exists()


@pytest.mark.parametrize(
    ("raw_flags", "expected_mode"),
    (
        (" --no-print-directory -qe", "query"),
        (" --no-print-directory -srnke", "dry-run"),
        (" --no-print-directory -tsrke", "touch"),
        ("en", "dry-run"),
        ("-I/tmp/owned-qnt", "execute"),
        ("--include-dir=/tmp/owned-qnt", "execute"),
        ("--no-print-directory -- hint=-qnt", "execute"),
        ("--no-print-directory -- hint= -qnt", "execute"),
        ("--no-print-directory --just-print -- hint=-q", "dry-run"),
        ("--no-print-directory ordinary-qnt-path", "execute"),
    ),
)
def test_actual_mode_classifier_keeps_literal_option_arguments_and_assignment_tail(
    development_repo: DevelopmentFixture,
    raw_flags: str,
    expected_mode: str,
) -> None:
    """Synthetic literal-input controls are distinct from native flag production."""
    fixture = development_repo
    control = fixture.root.parent / "literal-inspection-mode-control.mk"
    control.write_text(
        "override MAKEFLAGS := "
        + raw_flags
        + "\n"
        + _inspection_inputs(RECOVERY.read_text(encoding="utf-8"))
        + "$(info owned-native-mode=$(_publication-native-mode))\n"
        + ".PHONY: mode-control\nmode-control:\n\t@true\n",
        encoding="utf-8",
    )
    result = fixture.invoke("mode-control", makefile=control)
    assert result.returncode in {0, 1}, result.stdout + result.stderr
    assert "owned-native-mode=" + expected_mode in result.stdout
    assert fixture.rows() == []


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("variable", (BRANCH_INPUT, SHA_INPUT))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_expected_pair_remains_literal_at_native_startup_and_publication(
    development_repo: DevelopmentFixture,
    origin: str,
    variable: str,
    payload_kind: str,
) -> None:
    fixture = development_repo
    marker = fixture.root / "unexpected evaluation"
    quoted_marker = shlex.quote(str(marker))
    payload = (
        f"$(shell touch {quoted_marker})" if payload_kind == "make" else chr(96) + f"touch {quoted_marker}" + chr(96)
    )
    environment = dict(fixture.environment)
    arguments = [f"{BRANCH_INPUT}=development", f"{SHA_INPUT}={fixture.sha}"]
    arguments = [value for value in arguments if not value.startswith(variable + "=")]
    if origin == "argv":
        arguments.append(variable + "=" + payload)
    else:
        environment[variable] = payload
    control = fixture.root.parent / "unguarded-publication-control.mk"
    control.write_text(
        'literal-control:\n\t@printf \'%s\\n\' "$(PUBLISH_EXPECTED_BRANCH)" "$(PUBLISH_EXPECTED_SHA)"\n',
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control",
        *arguments,
        makefile=control,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), "the same payload/variable/origin must execute in native unguarded Make"
    marker.unlink()
    startup = fixture.invoke(
        "help",
        *arguments,
        makefile=ROOT / "Makefile",
        cwd=ROOT,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert startup.returncode == 0, startup.stdout + startup.stderr
    assert not marker.exists()
    result = fixture.invoke(
        "git-push-sandboxcom",
        *arguments,
        environment=environment,
        early_environment=origin == "environment-e",
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert "phase=expected-input" in result.stderr
    _not_started(fixture, "auth", "runtime", "transport", "verdict")


@pytest.mark.parametrize("record_arm_failure", [False, True], ids=["independent-source", "failed-first-arm"])
def test_owned_pending_build_allows_independent_new_branch_publication(
    feature_repo: DevelopmentFixture, record_arm_failure: bool,
) -> None:
    """Observe real A's queued Build before admitting B through the native guards."""
    fixture = feature_repo
    history = Path(fixture.environment["GLUDD_CI_HISTORY_FILE"])
    restart = Path(fixture.environment["GLUDD_CI_RESTART_COUNT_FILE"])
    runner = shlex.quote(sys.executable) + " " + shlex.quote(str(fixture.root / "native-boundary.py"))
    source = fixture.makefile.read_text(encoding="utf-8")
    guard_source = GUARDRAILS.read_text(encoding="utf-8")
    for name in ("_ci-verdict-history-guard", "_ci-restart-cap"):
        stub = f"{name}:\n\t@{runner} guard {name}"
        assert source.count(stub) == 1
        rule = _target(guard_source, name)
        if name == "_ci-verdict-history-guard":
            literal = "STATE_FILE=/tmp/gludd-ci-verdict-history.json;"
            assert rule.count(literal) == 1
            rule = rule.replace(literal, "STATE_FILE=" + shlex.quote(str(history)) + ";")
        header, body = rule.split("\n", 1)
        source = source.replace(stub, header + f"\n\t@{runner} guard {name}\n" + body)
    fixture.makefile.write_text(source, encoding="utf-8")
    gh = Path(fixture.environment["PATH"].split(os.pathsep)[0]) / "gh"
    gh_source = gh.read_text(encoding="utf-8")
    run_fields = "'status': 'queued', 'conclusion': '', 'event': 'workflow_dispatch'"
    assert gh_source.count(run_fields) == 1
    gh.write_text(
        gh_source.replace(run_fields, "'headBranch': os.environ['FEATURE_BRANCH'], "
                          "'workflowName': 'Build and Release', " + run_fields),
        encoding="utf-8",
    )

    published_a = fixture.invoke("git-push-sandboxcom", *_feature_arguments(fixture))
    assert published_a.returncode == 0, published_a.stdout + published_a.stderr
    sha_a = fixture.sha
    assert _feature_remote_sha(fixture) == sha_a
    assert restart.read_text(encoding="utf-8") == "1"
    observation = fixture.root.parent / "owned-pending-observation.json"
    state = Path(fixture.environment["GLUDD_CI_STATE_FILE"])
    before_state = state.read_bytes() if state.exists() else None
    wait = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci_await.py"),
         "--repo", "sandboxcom/gludd", "--ref", fixture.environment["FEATURE_BRANCH"],
         "--sha", sha_a, "--workflow", "Build and Release",
         "--event", "workflow_dispatch", "--timeout", "1", "--poll-interval", "0.05",
         "--observation-output", str(observation)],
        cwd=fixture.root, env=fixture.environment, capture_output=True, text=True,
        check=False, timeout=10,
    )
    assert wait.returncode == 2, wait.stdout + wait.stderr
    observed = json.loads(observation.read_text(encoding="utf-8"))
    assert observed["state"] == "timeout"
    assert observed["evidence"]["run"]["status"] == "queued"
    assert observed["release_ready"] is False
    assert restart.read_text(encoding="utf-8") == "1"
    assert (state.read_bytes() if state.exists() else None) == before_state

    prior_history = history.read_bytes()
    fixture.git("checkout", "--quiet", "-b", "feature/owned-parallel")
    if not record_arm_failure:
        fixture.git("-c", "commit.gpgSign=false", "commit", "--allow-empty",
                    "--quiet", "-m", "owned independent B")
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    assert (fixture.sha != sha_a) is (not record_arm_failure)
    fixture.environment["FEATURE_BRANCH"] = "feature/owned-parallel"
    arguments = _feature_arguments(fixture)
    if record_arm_failure:
        driver = fixture.root.parent / "owned-first-arm-driver.py"
        driver.write_text(
            "import importlib.util, os, pathlib, sys\n"
            "script = sys.argv.pop(1)\n"
            "sys.argv[0] = script\n"
            "if pathlib.Path(script).name != 'ci_check_cooldown.py':\n"
            "    os.execv(sys.executable, [sys.executable, *sys.argv])\n"
            "spec = importlib.util.spec_from_file_location('owned_writer', script)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "def fail_first_history(_history):\n"
            "    raise OSError('owned initial history arm failure')\n"
            "module.save_history_atomic = fail_first_history\n"
            "raise SystemExit(module.main())\n",
            encoding="utf-8",
        )
        arguments = [value for value in arguments if not value.startswith("PYTHON=")]
        arguments.append("PYTHON=" + shlex.quote(sys.executable) + " " + shlex.quote(str(driver)))
    published_b = fixture.invoke("git-push-sandboxcom", *arguments)
    if record_arm_failure:
        assert published_b.returncode != 0
        assert "owned initial history arm failure" in published_b.stdout + published_b.stderr
        for token in ("phase=verdict", "transport_may_have_applied=true", "reconcile"):
            assert token in published_b.stderr
        assert _feature_remote_sha(fixture) == sha_a
        assert history.read_bytes() == prior_history
        prior = json.loads(history.read_text())
        assert prior["diagnostic_push"]["branch"] == "feature/owned-source"
        assert prior["diagnostic_push"]["generation"] == prior["push_generation"] == 1
        assert prior["push_record_complete"] is True
        assert prior["last_verdict"] == "pending"
        assert restart.read_text() == "1"
        assert (state.read_bytes() if state.exists() else None) == before_state
        assert len(fixture.rows("transport")) == len(fixture.rows("hook")) == 2
        assert len(fixture.rows("dispatch")) == 1
        return
    assert published_b.returncode == 0, published_b.stdout + published_b.stderr
    assert _feature_remote_sha(fixture) == fixture.sha
    assert fixture.git("rev-parse", "refs/heads/feature/owned-source", bare=True).stdout.strip() == sha_a
    assert restart.read_text(encoding="utf-8") == "2"
    assert json.loads(history.read_text(encoding="utf-8"))["last_push_sha"] == fixture.sha
    assert len(fixture.rows("transport")) == 2
    assert len(fixture.rows("hook")) == 2
    assert all(sum(row["args"] == [guard] for row in fixture.rows("guard")) == 2
               for guard in ORIGINAL_GUARDS)


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
def test_native_recorder_uses_saved_feature_metadata_from_literal_inputs(
    development_repo: DevelopmentFixture, origin: str,
) -> None:
    fixture = development_repo
    history, restart = _install_native_recorder(fixture)
    values = {
        "PUSH_VERDICT_SHA": fixture.sha, "PUBLISH_EXPECTED_SHA": fixture.sha,
        "PUBLISH_EXPECTED_BRANCH": "feature/owned-feedback", "PUBLISH_PURPOSE": "feature-ci",
    }
    environment = dict(fixture.environment)
    arguments = ["PYTHON=" + shlex.quote(sys.executable)]
    if origin == "argv":
        arguments.extend(name + "=" + value for name, value in values.items())
    else:
        environment.update(values)

    result = fixture.invoke(
        "_record-push-verdict", *arguments, environment=environment,
        early_environment=origin == "environment-e",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = json.loads(history.read_text())
    assert recorded["last_push_sha"] == fixture.sha
    assert recorded["diagnostic_push"]["sha"] == fixture.sha
    assert recorded["diagnostic_push"]["branch"] == "feature/owned-feedback"
    assert recorded["diagnostic_push"]["purpose"] == "feature-ci"
    assert recorded["push_generation"] == recorded["diagnostic_push"]["generation"] == 1
    assert recorded["push_record_complete"] is True
    assert restart.read_text() == "1"
    _not_started(fixture, "auth", "runtime", "transport", "hook", "dispatch")


@pytest.mark.parametrize("origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("variable", ("PUBLISH_PURPOSE", BRANCH_INPUT, SHA_INPUT))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_native_recorder_metadata_stays_literal_with_same_origin_unguarded_witness(
    development_repo: DevelopmentFixture, origin: str, variable: str, payload_kind: str,
) -> None:
    fixture = development_repo
    history, restart = _install_native_recorder(fixture)
    marker = fixture.root / "owned unexpected metadata evaluation"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_kind == "make" else chr(96) + command + chr(96)
    values = {
        "PUSH_VERDICT_SHA": fixture.sha, "PUBLISH_EXPECTED_SHA": fixture.sha,
        "PUBLISH_EXPECTED_BRANCH": "feature/owned-feedback", "PUBLISH_PURPOSE": "feature-ci",
    }
    environment = dict(fixture.environment)
    arguments = ["PYTHON=" + shlex.quote(sys.executable)]
    arguments.extend(name + "=" + value for name, value in values.items() if name != variable)
    if origin == "argv":
        arguments.append(variable + "=" + payload)
    else:
        environment[variable] = payload
    control = fixture.root.parent / "owned-unguarded-record-metadata.mk"
    control.write_text(
        "literal-control:\n\t@printf '%s\\n' \"$(" + variable + ")\"\n",
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control", *arguments, makefile=control, environment=environment,
        early_environment=origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), "the identical metadata variable/payload/origin must execute unguarded"
    marker.unlink()

    result = fixture.invoke(
        "_record-push-verdict", *arguments, environment=environment,
        early_environment=origin == "environment-e",
    )

    assert result.returncode != 0
    assert "RECORD-PUSH-FAILED" in result.stdout + result.stderr
    assert not marker.exists()
    assert not history.exists() and not restart.exists()
    _not_started(fixture, "auth", "runtime", "transport", "hook", "dispatch")


def test_native_recorder_refuses_mismatched_saved_sha_before_history_write(
    development_repo: DevelopmentFixture,
) -> None:
    fixture = development_repo
    history, restart = _install_native_recorder(fixture)
    result = fixture.invoke(
        "_record-push-verdict", "PYTHON=" + shlex.quote(sys.executable),
        "PUSH_VERDICT_SHA=" + fixture.sha, "PUBLISH_EXPECTED_SHA=" + "b" * 40,
        "PUBLISH_EXPECTED_BRANCH=" + "feature/owned-feedback", "PUBLISH_PURPOSE=feature-ci",
    )
    assert result.returncode != 0
    assert "saved SHA does not match" in result.stderr
    assert not history.exists() and not restart.exists()
