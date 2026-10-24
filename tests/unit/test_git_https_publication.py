"""Native, confined Git controls for repository-local GitHub HTTPS publication."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "make/30-ci-and-release.mk"
LEGACY = ROOT / "tests/unit/test_verify_remote_recipe.py"
REAL_GIT = shutil.which("git")
REAL_MAKE = shutil.which("make")
SSH_URL = "git@github.com:sandboxcom/gludd.git"
HTTPS_URL = "https://github.com/sandboxcom/gludd.git"
TARGET = "git-configure-sandboxcom-https"
EARLY = """SANDBOXCOM_HTTPS_VALIDATE_ONLY ?= 1
override GLUDD_SANDBOXCOM_HTTPS_VALIDATE_ONLY := $(value SANDBOXCOM_HTTPS_VALIDATE_ONLY)
export GLUDD_SANDBOXCOM_HTTPS_VALIDATE_ONLY := $(value SANDBOXCOM_HTTPS_VALIDATE_ONLY)
unexport SANDBOXCOM_HTTPS_VALIDATE_ONLY
"""


def _body(source: str, target: str) -> str:
    marker = f"\n{target}:"
    assert source.count(marker) == 1
    tail = source.split(marker, 1)[1]
    return tail.split("\n\n", 1)[0]


@dataclass
class HttpsFixture:
    root: Path
    makefile: Path
    gh: Path
    calls: Path
    global_config: Path
    tripwire: Path
    environment: dict[str, str] = field(repr=False)
    sha: str
    old_sha: str

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert REAL_GIT is not None
        return subprocess.run(
            [REAL_GIT, *arguments], cwd=self.root, env=self.environment,
            capture_output=True, text=True, check=check, timeout=10,
        )

    def rows(self) -> list[dict[str, object]]:
        if not self.calls.exists():
            return []
        rows: list[dict[str, object]] = []
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            row: object = json.loads(line)
            assert isinstance(row, dict) and all(isinstance(key, str) for key in row)
            rows.append(row)
        return rows

    def bind_helper(self) -> None:
        key = "credential.https://github.com.helper"
        self.git("config", "--local", "--replace-all", key, "")
        self.git("config", "--local", "--add", key,
                 "!" + shlex.quote(str(self.gh)) + " auth git-credential")

    def invoke(
        self, target: str, *arguments: str, makefile: Path | None = None,
        environment: dict[str, str] | None = None, cwd: Path | None = None,
        early_environment: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert REAL_MAKE is not None
        index = self.root / ".git/index"
        checkpoint = self.root / "ignored-checkpoint"
        before_index = (index.read_bytes(), index.stat().st_mode)
        before_refs = self.git("for-each-ref", "--format=%(refname) %(objectname)").stdout
        before_worktree = (self.root / "tracked.txt").read_bytes()
        before_checkpoint = checkpoint.read_bytes()
        before_global = self.global_config.read_bytes()
        result = subprocess.run(
            [REAL_MAKE, "--no-print-directory", *(["-e"] if early_environment else []),
             "-f", str(makefile or self.makefile), target, *arguments],
            cwd=cwd or self.root, env=environment or self.environment,
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert (index.read_bytes(), index.stat().st_mode) == before_index
        assert self.git("for-each-ref", "--format=%(refname) %(objectname)").stdout == before_refs
        assert (self.root / "tracked.txt").read_bytes() == before_worktree
        assert checkpoint.read_bytes() == before_checkpoint
        assert self.global_config.read_bytes() == before_global
        assert not (Path(self.environment["HOME"]) / ".git-credentials").exists()
        return result


@pytest.fixture
def https_publication_repo(tmp_path: Path) -> HttpsFixture:
    assert REAL_GIT is not None and REAL_MAKE is not None
    repository = tmp_path / "owned repository"
    repository.mkdir()
    home = tmp_path / "owned-home"
    home.mkdir()
    binaries = tmp_path / "bin tools's"
    binaries.mkdir()
    gh = binaries / "gh"
    calls = tmp_path / "gh-calls.jsonl"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "data = sys.stdin.read() if args[:2] == ['auth', 'git-credential'] else ''\n"
        "with Path(os.environ['HTTPS_FIXTURE_GH_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps({'argv': args, 'executable': sys.argv[0], 'stdin': data}) + '\\n')\n"
        "if args == ['auth', 'status', '--active', '--hostname', 'github.com']:\n"
        "    code = int(os.environ.get('HTTPS_FIXTURE_GH_STATUS', '0'))\n"
        "    print('fixture-gh-auth-native-' + str(code), file=sys.stderr)\n"
        "    raise SystemExit(code)\n"
        "if args == ['auth', 'git-credential', 'erase']:\n"
        "    assert data.endswith('\\n')\n"
        "    fields = data.splitlines()\n"
        "    assert fields.count('protocol=https') == 1 and fields.count('host=github.com') == 1\n"
        "    allowed = {'protocol=https', 'host=github.com',\n"
        "               'capability[]=authtype', 'capability[]=state', ''}\n"
        "    assert set(fields) <= allowed\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(69)\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    tripwire = tmp_path / "global-helper-invoked"
    global_config = home / "global-gitconfig"
    global_config.write_text(
        "[credential]\n\thelper = !touch " + shlex.quote(str(tripwire)) + "\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_", "GH_", "GITHUB_"))
        and key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
    }
    environment.update({
        "HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "GH_CONFIG_DIR": str(home / "gh"), "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
        "GIT_OPTIONAL_LOCKS": "0", "PATH": str(binaries) + os.pathsep + environment.get("PATH", ""),
        "HTTPS_FIXTURE_GH_CALLS": str(calls), "HTTPS_FIXTURE_GH_STATUS": "0",
    })
    fixture = HttpsFixture(
        repository, repository / "Makefile", gh, calls, global_config, tripwire,
        environment, "", "",
    )
    fixture.git("-c", "init.templateDir=", "init", "-b", "topic")
    fixture.git("config", "--local", "core.hooksPath", os.devnull)
    tracked = repository / "tracked.txt"
    tracked.write_text("owned original\n", encoding="utf-8")
    fixture.git("add", "tracked.txt")
    commit = ("-c", "user.name=HTTPS fixture", "-c", "user.email=https@fixture.invalid",
              "-c", "commit.gpgSign=false", "commit", "-m", "owned fixture")
    fixture.git(*commit)
    fixture.old_sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.git("tag", "topic")
    tracked.write_text("owned changed\n", encoding="utf-8")
    fixture.git("add", "tracked.txt")
    fixture.git(*commit)
    fixture.sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    (repository / ".git/info").mkdir(exist_ok=True)
    (repository / ".git/info/exclude").write_text("ignored-checkpoint\n", encoding="utf-8")
    (repository / "ignored-checkpoint").write_bytes(b"retained checkpoint payload\n")
    fixture.git("remote", "add", "sandboxcom", SSH_URL)
    fixture.makefile.write_text(
        EARLY + f"\nSYSTEM_PYTHON := {sys.executable}\nSSH_KEY := {repository / 'absent-key'}\n"
        + SOURCE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return fixture


@pytest.mark.parametrize("https_publication_plan_mode", [None, "1"], ids=["default", "explicit"])
def test_https_configuration_plan_is_readonly(
    https_publication_repo: HttpsFixture, https_publication_plan_mode: str | None,
) -> None:
    fixture = https_publication_repo
    before = (fixture.root / ".git/config").read_bytes()
    arguments = () if https_publication_plan_mode is None else (
        f"SANDBOXCOM_HTTPS_VALIDATE_ONLY={https_publication_plan_mode}",
    )
    result = fixture.invoke(TARGET, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (fixture.root / ".git/config").read_bytes() == before
    assert fixture.rows() == []
    assert "writes=false" in result.stdout
    assert "outcome=planned native_status=0" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


def test_https_configuration_applies_local_only_and_binds_native_helper(
    https_publication_repo: HttpsFixture,
) -> None:
    fixture = https_publication_repo
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.git("remote", "get-url", "--all", "sandboxcom").stdout.strip() == HTTPS_URL
    assert fixture.git("remote", "get-url", "--push", "--all", "sandboxcom").stdout.strip() == HTTPS_URL
    assert fixture.git("config", "--local", "--get-all", "credential.https://github.com.helper").stdout == (
        "\n!" + shlex.quote(str(fixture.gh)) + " auth git-credential\n"
    )
    assert "outcome=configured native_status=0" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    configured = (fixture.root / ".git/config").read_bytes()
    again = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert again.returncode == 0, again.stdout + again.stderr
    assert (fixture.root / ".git/config").read_bytes() == configured
    # Native Git invokes the configured absolute helper even after PATH loses it.
    environment = {**fixture.environment, "PATH": "/usr/bin:/bin"}
    assert REAL_GIT is not None
    rejected = subprocess.run(
        [REAL_GIT, "credential", "reject"], cwd=fixture.root, env=environment,
        input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True,
        check=False, timeout=10,
    )
    assert rejected.returncode == 0 and rejected.stdout == "" and rejected.stderr == ""
    assert not fixture.tripwire.exists()
    helper_call = fixture.rows()[-1]
    assert helper_call["argv"] == ["auth", "git-credential", "erase"]
    assert helper_call["executable"] == str(fixture.gh)
    request = helper_call["stdin"]
    assert isinstance(request, str) and request.endswith("\n")
    fields = request.splitlines()
    assert fields.count("protocol=https") == 1 and fields.count("host=github.com") == 1
    assert set(fields) <= {
        "protocol=https", "host=github.com", "capability[]=authtype", "capability[]=state", "",
    }


@pytest.mark.parametrize("https_publication_auth_status", [1, 17])
def test_https_configuration_retains_auth_failure_before_mutation(
    https_publication_repo: HttpsFixture, https_publication_auth_status: int,
) -> None:
    fixture = https_publication_repo
    fixture.environment["HTTPS_FIXTURE_GH_STATUS"] = str(https_publication_auth_status)
    before = (fixture.root / ".git/config").read_bytes()
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode != 0
    assert f"native_status={https_publication_auth_status}" in result.stderr
    assert f"fixture-gh-auth-native-{https_publication_auth_status}" in result.stderr
    assert f"Error {https_publication_auth_status}" in result.stderr
    assert (fixture.root / ".git/config").read_bytes() == before


@pytest.mark.parametrize("https_publication_bad_remote", [
    "https://example.invalid/sandboxcom/gludd.git",
    "https://github.com/another/repo.git", "http://github.com/sandboxcom/gludd.git",
])
def test_https_configuration_refuses_unknown_remote_before_auth(
    https_publication_repo: HttpsFixture, https_publication_bad_remote: str,
) -> None:
    fixture = https_publication_repo
    fixture.git("remote", "set-url", "sandboxcom", https_publication_bad_remote)
    before = (fixture.root / ".git/config").read_bytes()
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode != 0 and "outcome=refused native_status=2" in result.stderr
    assert (fixture.root / ".git/config").read_bytes() == before
    assert fixture.rows() == []


@pytest.mark.parametrize("https_publication_binding_case", ["multi-fetch", "multi-push", "rewrite"])
def test_https_configuration_refuses_ambiguous_or_rewritten_urls(
    https_publication_repo: HttpsFixture, https_publication_binding_case: str,
) -> None:
    fixture = https_publication_repo
    if https_publication_binding_case == "multi-fetch":
        fixture.git("remote", "set-url", "--add", "sandboxcom", HTTPS_URL)
    elif https_publication_binding_case == "multi-push":
        fixture.git("remote", "set-url", "--push", "--add", "sandboxcom", SSH_URL)
        fixture.git("remote", "set-url", "--push", "--add", "sandboxcom", HTTPS_URL)
    else:
        fixture.git("config", "--local", "url.file:///unowned.insteadOf", HTTPS_URL)
    before = (fixture.root / ".git/config").read_bytes()
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode != 0 and "outcome=refused native_status=2" in result.stderr
    assert (fixture.root / ".git/config").read_bytes() == before and fixture.rows() == []


def test_https_configuration_preserves_native_config_lock_failure(
    https_publication_repo: HttpsFixture,
) -> None:
    fixture = https_publication_repo
    lock = fixture.root / ".git/config.lock"
    lock.write_bytes(b"owned competing configuration checkpoint\n")
    before = (fixture.root / ".git/config").read_bytes()
    native = fixture.git("config", "--local", "credential.https://github.com.helper", "", check=False)
    assert native.returncode != 0
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode != 0 and f"native_status={native.returncode}" in result.stderr
    assert f"Error {native.returncode}" in result.stderr
    assert (fixture.root / ".git/config").read_bytes() == before
    assert lock.read_bytes() == b"owned competing configuration checkpoint\n"


def test_verify_https_retains_native_auth_error_without_ssh_key(
    https_publication_repo: HttpsFixture,
) -> None:
    fixture = https_publication_repo
    fixture.git("remote", "set-url", "sandboxcom", HTTPS_URL)
    fixture.bind_helper()
    fixture.environment["HTTPS_FIXTURE_GH_STATUS"] = "17"
    result = fixture.invoke("verify-remote", "BRANCH=topic", f"SHA={fixture.sha}")
    assert result.returncode != 0
    assert "fixture-gh-auth-native-17" in result.stderr
    assert "native_status=17" in result.stderr and "Error 17" in result.stderr
    assert "SSH key is missing" not in result.stdout + result.stderr
    assert "VERIFIED " not in result.stdout


def test_verify_https_reaches_native_git_and_preserves_transport_failure(
    https_publication_repo: HttpsFixture,
) -> None:
    fixture = https_publication_repo
    fixture.git("remote", "set-url", "sandboxcom", HTTPS_URL)
    fixture.bind_helper()
    result = fixture.invoke("verify-remote", "BRANCH=topic", f"SHA={fixture.sha}")
    assert result.returncode != 0 and "transport 'https' not allowed" in result.stderr
    assert "native_status=128" in result.stderr and "Error 128" in result.stderr
    assert fixture.rows()[0]["argv"] == ["auth", "status", "--active", "--hostname", "github.com"]
    assert "VERIFIED " not in result.stdout


def test_https_configuration_offline_plan_and_missing_gh_apply(
    https_publication_repo: HttpsFixture, tmp_path: Path,
) -> None:
    fixture = https_publication_repo
    assert REAL_GIT is not None
    tools = tmp_path / "native-git-only"
    tools.mkdir()
    (tools / "git").symlink_to(REAL_GIT)
    fixture.environment["PATH"] = str(tools)
    before = (fixture.root / ".git/config").read_bytes()
    planned = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=1")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    result = fixture.invoke(TARGET, "SANDBOXCOM_HTTPS_VALIDATE_ONLY=0")
    assert result.returncode != 0 and "native_status=127" in result.stderr
    assert "Error 127" in result.stderr and fixture.rows() == []
    assert (fixture.root / ".git/config").read_bytes() == before


def test_verify_https_requires_the_repo_local_helper_before_authentication(
    https_publication_repo: HttpsFixture,
) -> None:
    fixture = https_publication_repo
    fixture.git("remote", "set-url", "sandboxcom", HTTPS_URL)
    result = fixture.invoke("verify-remote", "BRANCH=topic", f"SHA={fixture.sha}")
    assert result.returncode != 0 and "native_status=1" in result.stderr
    assert "phase=helper-binding" in result.stderr and "VERIFIED " not in result.stdout
    assert fixture.rows() == []


@pytest.mark.parametrize("https_publication_key_exists", [False, True])
def test_verify_ssh_retains_original_key_requirement(
    https_publication_repo: HttpsFixture, https_publication_key_exists: bool,
) -> None:
    fixture = https_publication_repo
    key = fixture.root / "test-key"
    if https_publication_key_exists:
        key.write_bytes(b"fixture availability marker; not a credential\n")
    result = fixture.invoke("verify-remote", f"SSH_KEY={key}", "BRANCH=topic", f"SHA={fixture.sha}")
    assert result.returncode != 0 and fixture.rows() == []
    if https_publication_key_exists:
        assert "SSH key available" in result.stdout
        assert "transport 'ssh' not allowed" in result.stderr
        assert "native_status=128" in result.stderr
    else:
        assert "SSH key is missing or unreadable" in result.stdout
        assert "Error 1" in result.stderr
    assert "VERIFIED " not in result.stdout


_LEGACY_NAMES = (
    "test_verify_remote_target_exists", "test_recipe_pins_to_heads_ref",
    "test_recipe_supports_sha_parameter", "test_recipe_supports_branch_parameter",
    "test_recipe_emits_verified_on_success", "test_recipe_emits_remote_mismatch_on_failure",
    "test_recipe_shortens_remote_to_sha_length", "test_no_bare_branch_lookup_remains",
)


@pytest.mark.parametrize("https_publication_legacy_assertion", _LEGACY_NAMES)
def test_original_verify_recipe_assertion_is_unchanged(
    https_publication_repo: HttpsFixture, https_publication_legacy_assertion: str,
) -> None:
    spec = importlib.util.spec_from_file_location("https_legacy_verify_assertions", LEGACY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vars(module)["MAKEFILE"] = https_publication_repo.makefile
    factory: object = module.TestVerifyRemoteRecipe
    assert callable(factory)
    instance = cast(Callable[[], object], factory)()
    assertion: object = getattr(instance, https_publication_legacy_assertion)
    assert callable(assertion)
    cast(Callable[[], None], assertion)()


@pytest.mark.parametrize("https_publication_receipt_case", ["head", "short", "tag-mismatch", "missing", "transport"])
def test_actual_receipt_body_uses_native_git_heads_and_status(
    https_publication_repo: HttpsFixture, tmp_path: Path, https_publication_receipt_case: str,
) -> None:
    fixture = https_publication_repo
    bare = tmp_path / "owned-remote.git"
    fixture.git("clone", "--bare", "--no-hardlinks", str(fixture.root), str(bare))
    fixture.git("remote", "set-url", "sandboxcom", str(bare))
    # This probe binds only the actual result recipe; canonical auth/routing is
    # tested independently above. It is not live canonical HTTPS acceptance.
    source = SOURCE.read_text(encoding="utf-8")
    body = _body(source, "verify-remote").split("\n", 1)[1]
    support = ""
    marker = "\ndefine _sandboxcom_publication_failure\n"
    if marker in source:
        support = (
            "define _sandboxcom_publication_failure\n"
            + source.split(marker, 1)[1].split("\nendef", 1)[0]
            + "\nendef\n"
        )
    proof = tmp_path / "native-receipt.mk"
    proof.write_text(support + "verify-remote:\n" + body + "\n", encoding="utf-8")
    sha = fixture.old_sha if https_publication_receipt_case == "tag-mismatch" else fixture.sha
    if https_publication_receipt_case == "short":
        sha = sha[:12]
    branch = "absent" if https_publication_receipt_case == "missing" else "topic"
    if https_publication_receipt_case == "transport":
        fixture.git("remote", "set-url", "sandboxcom", HTTPS_URL)
    result = fixture.invoke("verify-remote", f"BRANCH={branch}", f"SHA={sha}", makefile=proof)
    if https_publication_receipt_case in {"head", "short"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"VERIFIED topic@{sha}" in result.stdout
        assert "outcome=verified native_status=0" in result.stderr
    else:
        expected = {"tag-mismatch": 1, "missing": 2, "transport": 128}[https_publication_receipt_case]
        assert result.returncode != 0
        assert f"native_status={expected}" in result.stderr and f"Error {expected}" in result.stderr
        assert "VERIFIED " not in result.stdout


def _startup_makefile(tmp_path: Path) -> Path:
    return ROOT / "Makefile"


@pytest.mark.parametrize("https_publication_literal_kind", ["make-shell", "shell", "backticks"])
@pytest.mark.parametrize("https_publication_literal_origin", ["command-line", "environment"])
@pytest.mark.parametrize("https_publication_literal_e", [False, True])
@pytest.mark.parametrize("https_publication_literal_main", [False, True], ids=["isolated-owner", "actual-main-startup"])
def test_https_mode_is_literal_before_native_startup_and_recipe(
    https_publication_repo: HttpsFixture, tmp_path: Path,
    https_publication_literal_kind: str, https_publication_literal_origin: str,
    https_publication_literal_e: bool, https_publication_literal_main: bool,
) -> None:
    fixture = https_publication_repo
    marker = tmp_path / "owned input-executed marker"
    quoted_marker = shlex.quote(str(marker))
    if https_publication_literal_kind == "make-shell":
        value = f"$(shell touch {quoted_marker})"
    elif https_publication_literal_kind == "shell":
        value = f"$$(touch {quoted_marker})"
    else:
        value = chr(96) + f"touch {quoted_marker}" + chr(96)
    environment = dict(fixture.environment)
    arguments: tuple[str, ...] = ()
    if https_publication_literal_origin == "environment":
        environment["SANDBOXCOM_HTTPS_VALIDATE_ONLY"] = value
    else:
        arguments = (f"SANDBOXCOM_HTTPS_VALIDATE_ONLY={value}",)
    unguarded = tmp_path / "unguarded-mode.mk"
    unguarded.write_text(
        TARGET + ':\n\t@printf \'%s\\n\' "$(SANDBOXCOM_HTTPS_VALIDATE_ONLY)" >/dev/null\n',
        encoding="utf-8",
    )
    witness = fixture.invoke(
        TARGET, *arguments, makefile=unguarded, environment=environment,
        cwd=ROOT if https_publication_literal_main else fixture.root,
        early_environment=https_publication_literal_e,
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.is_file(), witness.stdout + witness.stderr
    assert fixture.rows() == []
    marker.unlink()

    result = fixture.invoke(
        TARGET, *arguments,
        makefile=_startup_makefile(tmp_path) if https_publication_literal_main else fixture.makefile,
        environment=environment, cwd=ROOT if https_publication_literal_main else fixture.root,
        early_environment=https_publication_literal_e,
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert "outcome=refused native_status=2" in result.stderr
    assert "must be 0 or 1" in result.stderr
    assert fixture.rows() == []


def test_https_fixture_environment_repr_is_private_with_synthetic_input(tmp_path: Path) -> None:
    """Establish failure privacy before any fixture copies the real environment."""
    synthetic = {"HTTPS_FIXTURE_CONTROLLED_CARRIER": "owned-synthetic-repr-canary"}
    fixture = HttpsFixture(
        tmp_path, tmp_path / "Makefile", tmp_path / "gh", tmp_path / "calls",
        tmp_path / "global", tmp_path / "tripwire", synthetic, "a" * 40, "b" * 40,
    )
    assert fixture.environment is synthetic
    assert fixture.environment == {"HTTPS_FIXTURE_CONTROLLED_CARRIER": "owned-synthetic-repr-canary"}
    rendered = repr(fixture)
    assert "owned-synthetic-repr-canary" not in rendered
    assert "HTTPS_FIXTURE_CONTROLLED_CARRIER" not in rendered
    assert "environment=" not in rendered


@pytest.mark.parametrize("https_publication_bootstrap_e", [False, True])
def test_https_auth_bootstrap_ignores_implicit_cwd_and_preserves_carriers(
    https_publication_repo: HttpsFixture, tmp_path: Path, https_publication_bootstrap_e: bool,
) -> None:
    """An active same-entrypoint witness distinguishes -P from erased carriers."""
    fixture = https_publication_repo
    fixture.git("remote", "set-url", "sandboxcom", HTTPS_URL)
    fixture.bind_helper()
    before_config = (fixture.root / ".git/config").read_bytes()
    marker = tmp_path / "owned-shlex-executed.json"
    startup_trace = tmp_path / "explicit-startup-carriers.jsonl"
    carrier = tmp_path / "explicit unrelated python carrier"
    carrier.mkdir()
    (carrier / "sitecustomize.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"with Path({str(startup_trace)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'inline': '-c' in sys.orig_argv, "
        "'argv': sys.orig_argv, 'safe_path': sys.flags.safe_path, "
        "'cwd': os.getcwd(), 'pythonpath': os.environ.get('PYTHONPATH'), "
        "'carrier': os.environ.get('HTTPS_BOOTSTRAP_CARRIER')}) + '\\n')\n",
        encoding="utf-8",
    )
    (fixture.root / "shlex.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{"
        "'argv': sys.orig_argv, 'cwd': os.getcwd(), "
        "'pythonpath': os.environ.get('PYTHONPATH'), "
        "'carrier': os.environ.get('HTTPS_BOOTSTRAP_CARRIER')"
        "}), encoding='utf-8')\n"
        "raise RuntimeError('owned active CWD shlex payload')\n",
        encoding="utf-8",
    )
    environment = dict(fixture.environment)
    environment["PYTHONPATH"] = str(carrier)
    environment["HTTPS_BOOTSTRAP_CARRIER"] = "explicit unrelated carrier  n q t"
    # Own the safe-path input for both carriers; production must preserve it.
    environment["PYTHONSAFEPATH"] = ""
    source = fixture.makefile.read_text(encoding="utf-8")
    unsafe = '"$(SYSTEM_PYTHON)" -c \'import shlex, sys;'
    guarded = '"$(SYSTEM_PYTHON)" -P -c \'import shlex, sys;'
    assert source.count(unsafe) + source.count(guarded) == 1
    unguarded = tmp_path / "unguarded-native-auth.mk"
    unguarded.write_text(source.replace(guarded, unsafe), encoding="utf-8")
    witness = fixture.invoke(
        "_require-sandboxcom-git-auth", makefile=unguarded, environment=environment,
        early_environment=https_publication_bootstrap_e,
    )
    assert witness.returncode != 0
    assert "owned active CWD shlex payload" in witness.stderr
    assert marker.is_file(), witness.stdout + witness.stderr
    payload: dict[str, object] = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["argv"] == [
        sys.executable, "-c",
        'import shlex, sys; print("!" + shlex.quote(sys.argv[1]) + " auth git-credential")',
        str(fixture.gh),
    ]
    assert payload["cwd"] == str(fixture.root)
    assert payload["pythonpath"] == str(carrier)
    assert payload["carrier"] == "explicit unrelated carrier  n q t"
    assert fixture.rows() == []
    marker.unlink()

    result = fixture.invoke(
        "_require-sandboxcom-git-auth", environment=environment,
        early_environment=https_publication_bootstrap_e,
    )
    assert not marker.exists(), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["argv"] for row in fixture.rows()] == [
        ["auth", "status", "--active", "--hostname", "github.com"],
    ]
    assert (fixture.root / ".git/config").read_bytes() == before_config
    startup: list[dict[str, object]] = [
        json.loads(line) for line in startup_trace.read_text(encoding="utf-8").splitlines()
    ]
    inline = [row for row in startup if row["inline"]]
    assert len(inline) == 2
    assert [row["safe_path"] for row in inline] == [False, True]
    assert all(row["cwd"] == str(fixture.root) for row in inline)
    assert all(row["pythonpath"] == str(carrier) for row in inline)
    assert all(row["carrier"] == "explicit unrelated carrier  n q t" for row in inline)
