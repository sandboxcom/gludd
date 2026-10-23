"""Confined native controls for operator-owned GitHub workflow consent."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from tests.unit.test_git_https_publication import HttpsFixture
from tests.unit.test_git_https_publication import (
    https_publication_repo as https_publication_repo,
)

ROOT = Path(__file__).resolve().parents[2]
TARGET = "github-workflow-auth"
STATUS_TARGET = "github-auth-status"
OBSERVE = ["auth", "status", "--hostname", "github.com"]
MODE = "GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY"
ACCOUNT = "GITHUB_WORKFLOW_AUTH_ACCOUNT"
EARLY = """GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY ?= 1
GITHUB_WORKFLOW_AUTH_ACCOUNT ?=
override GLUDD_GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY := $(value GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY)
export GLUDD_GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY := $(value GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY)
override GLUDD_GITHUB_WORKFLOW_AUTH_ACCOUNT := $(value GITHUB_WORKFLOW_AUTH_ACCOUNT)
export GLUDD_GITHUB_WORKFLOW_AUTH_ACCOUNT := $(value GITHUB_WORKFLOW_AUTH_ACCOUNT)
unexport GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY GITHUB_WORKFLOW_AUTH_ACCOUNT
"""
STATUS = ["auth", "status", "--active", "--hostname", "github.com"]
IDENTITY = ["api", "user", "--hostname", "github.com", "--jq", ".login"]
REFRESH = ["auth", "refresh", "--hostname", "github.com", "--scopes", "workflow"]
EXPECTED = [STATUS, IDENTITY, REFRESH, STATUS, IDENTITY]
GH_STUB = r"""import json, os, sys
from pathlib import Path
args = sys.argv[1:]
calls = Path(os.environ["HTTPS_FIXTURE_GH_CALLS"])
with calls.open("a") as output:
    output.write(json.dumps({"argv": args, "executable": sys.argv[0], "stdin": ""}) + "\n")
rows = [json.loads(line)["argv"] for line in calls.read_text().splitlines()]
status = ["auth", "status", "--active", "--hostname", "github.com"]
observe = ["auth", "status", "--hostname", "github.com"]
identity = ["api", "user", "--hostname", "github.com", "--jq", ".login"]
refresh = ["auth", "refresh", "--hostname", "github.com", "--scopes", "workflow"]
if args == observe:
    phase = "observed-status"
elif args == status:
    phase = "pre-status" if rows.count(status) == 1 else "post-status"
elif args == identity:
    phase = "identity" if rows.count(identity) == 1 else "post-identity"
elif args == refresh:
    phase = "refresh"
    Path(os.environ["WORKFLOW_AUTH_FIXTURE_STATE"]).write_text("refresh attempted\n")
else:
    raise SystemExit(69)
if phase == os.environ.get("WORKFLOW_AUTH_FIXTURE_FAIL"):
    code = int(os.environ["WORKFLOW_AUTH_FIXTURE_CODE"])
    print("native-gh-" + phase + "-" + str(code), file=sys.stderr)
    raise SystemExit(code)
if phase == "identity":
    print(os.environ.get("WORKFLOW_AUTH_FIXTURE_LOGIN", "sandboxcom"))
elif phase == "post-identity":
    print(os.environ.get("WORKFLOW_AUTH_FIXTURE_POST_LOGIN",
                         os.environ.get("WORKFLOW_AUTH_FIXTURE_LOGIN", "sandboxcom")))
elif phase.endswith("status"):
    print("native redacted authentication status", file=sys.stderr)
else:
    print("native operator consent returned")
"""


@pytest.fixture
def workflow_auth_repo(https_publication_repo: HttpsFixture) -> HttpsFixture:
    fixture = https_publication_repo
    fixture.makefile.write_text(
        EARLY + fixture.makefile.read_text(encoding="utf-8"), encoding="utf-8"
    )
    fixture.gh.write_text(f"#!{sys.executable}\n" + GH_STUB, encoding="utf-8")
    fixture.environment.pop("MAKEOVERRIDES", None)
    fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"] = str(
        fixture.root / "native-auth-state"
    )
    return fixture


def _arguments(workflow_plan_mode: str = "0", account: str = "sandboxcom") -> tuple[str, str]:
    return f"{MODE}={workflow_plan_mode}", f"{ACCOUNT}={account}"


def _argv(fixture: HttpsFixture) -> list[object]:
    return [row["argv"] for row in fixture.rows()]


def test_github_auth_status_owner_observes_without_consent(
    workflow_auth_repo: HttpsFixture,
) -> None:
    fixture = workflow_auth_repo

    result = fixture.invoke(STATUS_TARGET)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _argv(fixture) == [OBSERVE]
    assert not Path(fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"]).exists()
    assert "outcome=observed native_status=0" in result.stderr
    assert "zero exit" in result.stderr
    assert "workflow write" in result.stderr
    assert TARGET in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


def test_github_auth_status_owner_retains_native_failure(
    workflow_auth_repo: HttpsFixture,
) -> None:
    fixture = workflow_auth_repo
    fixture.environment.update({
        "WORKFLOW_AUTH_FIXTURE_FAIL": "observed-status",
        "WORKFLOW_AUTH_FIXTURE_CODE": "37",
    })

    result = fixture.invoke(STATUS_TARGET)

    assert result.returncode != 0
    assert _argv(fixture) == [OBSERVE]
    assert "native-gh-observed-status-37" in result.stderr
    assert "native_status=37" in result.stderr
    assert "credentials_may_have_changed=false" in result.stderr
    assert "outcome=observed" not in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


@pytest.mark.parametrize("workflow_plan_mode", (None, "1"), ids=("default", "explicit"))
def test_workflow_auth_preview_has_no_provider_or_credential_io(
    workflow_auth_repo: HttpsFixture, workflow_plan_mode: str | None
) -> None:
    fixture = workflow_auth_repo
    arguments: tuple[str, ...] = (f"{ACCOUNT}=sandboxcom",)
    if workflow_plan_mode is not None:
        arguments += (f"{MODE}={workflow_plan_mode}",)

    result = fixture.invoke(TARGET, *arguments)

    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.rows() == []
    assert not Path(fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"]).exists()
    assert "additional_scope=workflow" in result.stdout
    assert "credentials_changed=false auth_checked=false" in result.stdout
    assert "outcome=planned native_status=0" in result.stderr
    assert "operator" in result.stderr.lower()
    assert "gate_evidence=false release_ready=false" in result.stderr


def test_workflow_auth_default_preview_needs_no_selected_account(
    workflow_auth_repo: HttpsFixture,
) -> None:
    result = workflow_auth_repo.invoke(TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert workflow_auth_repo.rows() == []
    assert "outcome=planned" in result.stderr


def test_workflow_auth_apply_requires_explicit_existing_account(
    workflow_auth_repo: HttpsFixture,
) -> None:
    result = workflow_auth_repo.invoke(TARGET, *_arguments(account=""))
    assert result.returncode != 0
    assert workflow_auth_repo.rows() == []
    assert "phase=account-input" in result.stderr
    assert "credentials_may_have_changed=false" in result.stderr


@pytest.mark.parametrize("account", ("sandboxcom", "release-collaborator"))
def test_workflow_auth_uses_fixed_native_flow_and_retains_git_state(
    workflow_auth_repo: HttpsFixture, account: str
) -> None:
    fixture = workflow_auth_repo
    fixture.environment["WORKFLOW_AUTH_FIXTURE_LOGIN"] = account

    result = fixture.invoke(TARGET, *_arguments(account=account))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _argv(fixture) == EXPECTED
    assert Path(fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"]).read_text() == (
        "refresh attempted\n"
    )
    assert "outcome=returned native_status=0" in result.stderr
    assert "verify-state" in result.stderr
    assert "batch-push" in result.stderr
    assert "exact-SHA" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


@pytest.mark.parametrize(
    ("failure_phase", "owner_phase", "native_status", "call_count", "may_change"),
    (
        ("pre-status", "authentication", 37, 1, "false"),
        ("identity", "account-identity", 31, 2, "false"),
        ("refresh", "consent", 43, 3, "true"),
        ("post-status", "post-authentication", 29, 4, "true"),
        ("post-identity", "post-identity", 41, 5, "true"),
    ),
)
def test_workflow_auth_preserves_native_failure_and_stops_later_phases(
    workflow_auth_repo: HttpsFixture,
    failure_phase: str,
    owner_phase: str,
    native_status: int,
    call_count: int,
    may_change: str,
) -> None:
    fixture = workflow_auth_repo
    fixture.environment.update({
        "WORKFLOW_AUTH_FIXTURE_FAIL": failure_phase,
        "WORKFLOW_AUTH_FIXTURE_CODE": str(native_status),
    })

    result = fixture.invoke(TARGET, *_arguments())

    assert result.returncode != 0
    assert _argv(fixture) == EXPECTED[:call_count]
    assert f"native-gh-{failure_phase}-{native_status}" in result.stderr
    assert f"native_status={native_status}" in result.stderr
    assert f"phase={owner_phase}" in result.stderr
    assert f"credentials_may_have_changed={may_change}" in result.stderr
    assert "outcome=returned" not in result.stderr
    assert "reconcile" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


@pytest.mark.parametrize("actual_account", ("different-user", ""))
def test_workflow_auth_rejects_unexpected_account_before_consent(
    workflow_auth_repo: HttpsFixture, actual_account: str
) -> None:
    fixture = workflow_auth_repo
    fixture.environment["WORKFLOW_AUTH_FIXTURE_LOGIN"] = actual_account

    result = fixture.invoke(TARGET, *_arguments())

    assert result.returncode != 0
    assert _argv(fixture) == EXPECTED[:2]
    assert "outcome=refused native_status=2" in result.stderr
    assert "credentials_may_have_changed=false" in result.stderr
    assert not Path(fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"]).exists()


def test_workflow_auth_retains_uncertain_write_after_account_changes(
    workflow_auth_repo: HttpsFixture,
) -> None:
    fixture = workflow_auth_repo
    fixture.environment["WORKFLOW_AUTH_FIXTURE_POST_LOGIN"] = "different-user"

    result = fixture.invoke(TARGET, *_arguments())

    assert result.returncode != 0
    assert _argv(fixture) == EXPECTED
    assert "outcome=refused native_status=2" in result.stderr
    assert "phase=post-identity" in result.stderr
    assert "credentials_may_have_changed=true" in result.stderr
    assert "reconcile" in result.stderr
    assert Path(fixture.environment["WORKFLOW_AUTH_FIXTURE_STATE"]).exists()


@pytest.mark.parametrize("workflow_plan_mode", ("", "2", "-1", "true", "01", "0 "))
def test_workflow_auth_refuses_nonliteral_modes_before_provider_io(
    workflow_auth_repo: HttpsFixture, workflow_plan_mode: str
) -> None:
    result = workflow_auth_repo.invoke(TARGET, *_arguments(workflow_plan_mode))
    assert result.returncode != 0
    assert workflow_auth_repo.rows() == []
    assert "phase=validate-mode" in result.stderr


@pytest.mark.parametrize("argument_origin", ("argv", "environment", "environment-e"))
@pytest.mark.parametrize("variable", (MODE, ACCOUNT))
@pytest.mark.parametrize("payload_kind", ("make", "shell"))
def test_workflow_auth_native_startup_keeps_public_inputs_literal(
    workflow_auth_repo: HttpsFixture,
    argument_origin: str,
    variable: str,
    payload_kind: str,
) -> None:
    fixture = workflow_auth_repo
    marker = fixture.root / "unexpected-evaluation"
    quoted_marker = shlex.quote(str(marker))
    payload = (
        f"$(shell touch {quoted_marker})" if payload_kind == "make"
        else chr(96) + f"touch {quoted_marker}" + chr(96)
    )
    environment = dict(fixture.environment)
    arguments = list(_arguments("1"))
    if argument_origin == "argv":
        arguments = [
            argument for argument in arguments if not argument.startswith(variable + "=")
        ]
        arguments.append(f"{variable}={payload}")
    else:
        environment[variable] = payload
        arguments = [
            argument for argument in arguments if not argument.startswith(variable + "=")
        ]

    control_makefile = fixture.root.parent / "workflow-literal-control.mk"
    control_makefile.write_text(
        "literal-control:\n"
        "\t@printf '%s\\n' \"$(GITHUB_WORKFLOW_AUTH_VALIDATE_ONLY)\" "
        "\"$(GITHUB_WORKFLOW_AUTH_ACCOUNT)\"\n",
        encoding="utf-8",
    )
    witness = fixture.invoke(
        "literal-control", *arguments, makefile=control_makefile,
        environment=environment, early_environment=argument_origin == "environment-e",
    )
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert marker.exists(), (
        "the unguarded native control must evaluate this exact payload\n"
        + witness.stdout + witness.stderr
    )
    marker.unlink()

    result = fixture.invoke(
        TARGET, *arguments, makefile=ROOT / "Makefile", cwd=ROOT,
        environment=environment, early_environment=argument_origin == "environment-e",
    )

    assert not marker.exists()
    assert fixture.rows() == []
    if variable == MODE:
        assert result.returncode != 0
        assert "phase=validate-mode" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert payload in result.stdout


@pytest.mark.parametrize("gh_resolution", ("missing", "function"))
def test_workflow_auth_requires_an_absolute_available_gh(
    workflow_auth_repo: HttpsFixture, gh_resolution: str
) -> None:
    fixture = workflow_auth_repo
    environment = dict(fixture.environment)
    shell_arguments: tuple[str, ...] = ()
    if gh_resolution == "missing":
        fixture.gh.unlink()
        environment["PATH"] = str(fixture.gh.parent)
    else:
        owned_shell = fixture.root.parent / "workflow-auth-shell"
        owned_shell.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "arguments = sys.argv[1:]\n"
            "if arguments and arguments[0] == '-c':\n"
            "    prefix = \"gh() { printf '%s\\\\n' "
            "'{\\\"argv\\\":[\\\"unexpected-native-function\\\"]}' "
            ">> \\\"$HTTPS_FIXTURE_GH_CALLS\\\"; return 69; }; \"\n"
            "    arguments[1] = prefix + arguments[1]\n"
            "os.execv('/bin/sh', ['/bin/sh', *arguments])\n",
            encoding="utf-8",
        )
        owned_shell.chmod(0o700)
        shell_arguments = (f"SHELL={owned_shell}",)

    result = fixture.invoke(
        TARGET, *_arguments(), *shell_arguments, environment=environment
    )

    assert result.returncode != 0
    assert fixture.rows() == []
    assert "phase=gh-executable" in result.stderr
    assert "native_status=127" in result.stderr


def test_workflow_auth_contract_and_early_capture_are_registered() -> None:
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    rows = [row for row in contract["targets"] if row["name"] == TARGET]
    assert len(rows) == 1
    assert rows[0]["make_variables"] == [MODE, ACCOUNT]
    assert rows[0]["behavior"] == (
        f"make {TARGET} {MODE}=1 {ACCOUNT}=sandboxcom"
    )
    status_rows = [row for row in contract["targets"] if row["name"] == STATUS_TARGET]
    assert len(status_rows) == 1
    assert status_rows[0]["make_variables"] == []
    assert status_rows[0]["behavior"] == f"make {STATUS_TARGET}"
    foundation = (ROOT / "make/00-foundation.mk").read_text()
    first_native_shell = foundation.index("$(shell")
    for line in EARLY.splitlines():
        assert foundation.count(line) == 1
        assert foundation.index(line) < first_native_shell
