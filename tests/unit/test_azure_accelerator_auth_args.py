"""Hermetic contracts for Azure accelerator role and credential arguments."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import render_azure_accelerator_auth_args as subject

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
SP_NAME = "gludd accelerator 20260905"
ROLE_NAME = "General Ludd Accelerator Deployer"
SCOPE = f"/subscriptions/{SUBSCRIPTION_ID}"
OBSOLETE_PROVIDER_REGISTRATION = "Microsoft.Resources/subscriptions/providers/register/action"
REQUIRED_PROVIDER_REGISTRATIONS = frozenset(
    {
        "Microsoft.App/register/action",
        "Microsoft.Compute/register/action",
        "Microsoft.ContainerRegistry/register/action",
        "Microsoft.Insights/register/action",
        "Microsoft.Network/register/action",
        "Microsoft.OperationalInsights/register/action",
    }
)
ROLE_ARGS_PREFIX = (
    "role",
    "definition",
    "create",
    "--role-definition",
)
AUTH_ARGS = (
    "ad",
    "sp",
    "create-for-rbac",
    "--name",
    SP_NAME,
    "--role",
    ROLE_NAME,
    "--scopes",
    SCOPE,
    "--json-auth",
    "true",
    "--subscription",
    SUBSCRIPTION_ID,
    "--only-show-errors",
    "--output",
    "json",
)


def _decode(payload: bytes) -> tuple[str, ...]:
    """Decode one NUL-delimited argument stream."""
    return tuple(part.decode("utf-8") for part in payload.removesuffix(b"\0").split(b"\0"))


def test_role_arguments_materialize_the_checked_in_subscription_scope() -> None:
    arguments = subject.build_role_arguments(subscription_id=SUBSCRIPTION_ID)
    role = json.loads(arguments[4])

    assert arguments[:4] == ROLE_ARGS_PREFIX
    assert arguments[5:] == (
        "--subscription",
        SUBSCRIPTION_ID,
        "--only-show-errors",
        "--output",
        "json",
    )
    assert role["Name"] == ROLE_NAME
    assert role["AssignableScopes"] == [SCOPE]
    assert role["DataActions"] == []
    assert not any("CognitiveServices" in action for action in role["Actions"])
    assert OBSOLETE_PROVIDER_REGISTRATION not in role["Actions"]
    assert frozenset(role["Actions"]) >= REQUIRED_PROVIDER_REGISTRATIONS
    assert "{subscription_id}" not in arguments[4]


def test_auth_arguments_assign_the_accelerator_role_at_subscription_scope() -> None:
    arguments = subject.build_auth_arguments(
        subscription_id=SUBSCRIPTION_ID,
        service_principal_name=SP_NAME,
    )

    assert arguments == AUTH_ARGS
    assert arguments.count("--role") == 1
    assert arguments.count("--scopes") == 1
    assert "--skip-assignment" not in arguments
    assert "--json-auth" in arguments


def test_role_and_auth_streams_are_byte_exact_nul_delimited() -> None:
    role_arguments = subject.build_role_arguments(subscription_id=SUBSCRIPTION_ID)

    assert subject.render_role_arguments(subscription_id=SUBSCRIPTION_ID) == (
        b"\0".join(argument.encode("utf-8") for argument in role_arguments) + b"\0"
    )
    assert subject.render_auth_arguments(
        subscription_id=SUBSCRIPTION_ID,
        service_principal_name=SP_NAME,
    ) == b"\0".join(argument.encode("utf-8") for argument in AUTH_ARGS) + b"\0"


@pytest.mark.parametrize(
    "subscription_id",
    ["", "not-a-uuid", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", "../subscription"],
)
def test_invalid_subscription_is_rejected_before_output(subscription_id: str) -> None:
    with pytest.raises(ValueError, match="invalid subscription ID"):
        subject.render_role_arguments(subscription_id=subscription_id)
    with pytest.raises(ValueError, match="invalid subscription ID"):
        subject.render_auth_arguments(
            subscription_id=subscription_id,
            service_principal_name=SP_NAME,
        )


@pytest.mark.parametrize(
    "service_principal_name",
    ["", "--subscription", " leading-space", "trailing-space ", "contains\0nul", "x" * 121],
)
def test_invalid_or_option_shaped_principal_name_is_rejected(
    service_principal_name: str,
) -> None:
    with pytest.raises(ValueError, match="invalid service principal name"):
        subject.render_auth_arguments(
            subscription_id=SUBSCRIPTION_ID,
            service_principal_name=service_principal_name,
        )


@pytest.mark.parametrize(
    "replacement",
    [
        {},
        {"Name": "wrong", "AssignableScopes": ["/subscriptions/{subscription_id}"]},
        {
            "Name": ROLE_NAME,
            "AssignableScopes": ["/subscriptions/{subscription_id}"],
            "Actions": ["Microsoft.CognitiveServices/accounts/read"],
            "DataActions": [],
        },
    ],
)
def test_role_template_drift_fails_closed(tmp_path: Path, replacement: dict[str, object]) -> None:
    template = tmp_path / "role.json"
    template.write_text(json.dumps(replacement), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid accelerator role template"):
        subject.build_role_arguments(
            subscription_id=SUBSCRIPTION_ID,
            template_path=template,
        )


def test_missing_or_malformed_role_template_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")

    for template in (missing, malformed):
        with pytest.raises(ValueError, match="invalid accelerator role template"):
            subject.build_role_arguments(
                subscription_id=SUBSCRIPTION_ID,
                template_path=template,
            )


def test_role_template_rejects_obsolete_generic_provider_registration(tmp_path: Path) -> None:
    role = json.loads(subject.ROLE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    role["Actions"].append(OBSOLETE_PROVIDER_REGISTRATION)
    template = tmp_path / "obsolete-provider-registration.json"
    template.write_text(json.dumps(role), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid accelerator role template"):
        subject.build_role_arguments(
            subscription_id=SUBSCRIPTION_ID,
            template_path=template,
        )


def test_role_template_requires_each_provider_registration(tmp_path: Path) -> None:
    role = json.loads(subject.ROLE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    role["Actions"].remove("Microsoft.Network/register/action")
    template = tmp_path / "missing-provider-registration.json"
    template.write_text(json.dumps(role), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid accelerator role template"):
        subject.build_role_arguments(
            subscription_id=SUBSCRIPTION_ID,
            template_path=template,
        )


def test_main_modes_emit_only_the_requested_argument_stream(
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    monkeypatch.setenv("_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW", SUBSCRIPTION_ID)
    monkeypatch.setenv("_GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW", SP_NAME)

    assert subject.main(["role"]) == 0
    role_capture = capsysbinary.readouterr()
    assert _decode(role_capture.out) == subject.build_role_arguments(
        subscription_id=SUBSCRIPTION_ID
    )
    assert role_capture.err == b""

    assert subject.main(["auth"]) == 0
    auth_capture = capsysbinary.readouterr()
    assert _decode(auth_capture.out) == AUTH_ARGS
    assert auth_capture.err == b""


@pytest.mark.parametrize("argv", [[], ["unknown"], ["role", "extra"]])
def test_main_rejects_unknown_modes_without_stdout(
    argv: list[str],
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    assert subject.main(argv) == 2
    captured = capsysbinary.readouterr()

    assert captured.out == b""
    assert captured.err == b"azure-accelerator-auth-args: invalid mode\n"


def test_main_missing_environment_is_redacted_and_has_no_partial_stream(
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    monkeypatch.delenv("_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW", raising=False)
    monkeypatch.delenv("_GLUDD_AZURE_ACCELERATOR_SP_NAME_RAW", raising=False)

    assert subject.main(["role"]) == 2
    captured = capsysbinary.readouterr()

    assert captured.out == b""
    assert captured.err == (
        b"azure-accelerator-auth-args: AZURE_ACCELERATOR_SUBSCRIPTION_ID is required\n"
    )


def test_make_targets_emit_exact_role_and_auth_arguments() -> None:
    role_result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-accelerator-role-args",
            f"AZURE_ACCELERATOR_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    auth_result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-accelerator-auth-args",
            f"AZURE_ACCELERATOR_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_ACCELERATOR_SP_NAME={SP_NAME}",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert role_result.returncode == 0, role_result.stderr.decode(errors="replace")
    assert _decode(role_result.stdout) == subject.build_role_arguments(
        subscription_id=SUBSCRIPTION_ID
    )
    assert role_result.stderr == b""
    assert auth_result.returncode == 0, auth_result.stderr.decode(errors="replace")
    assert _decode(auth_result.stdout) == AUTH_ARGS
    assert auth_result.stderr == b""


@pytest.mark.parametrize(
    ("target", "variables"),
    [
        ("azure-accelerator-role-args", []),
        (
            "azure-accelerator-auth-args",
            [f"AZURE_ACCELERATOR_SUBSCRIPTION_ID={SUBSCRIPTION_ID}"],
        ),
    ],
)
def test_make_targets_reject_missing_inputs_without_partial_output(
    target: str,
    variables: list[str],
) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target, *variables],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == b""
    assert b" is required" in result.stderr


def test_both_streams_invoke_one_fake_azure_process(tmp_path: Path) -> None:
    fake_az = tmp_path / "az"
    invocation_log = tmp_path / "argv.jsonl"
    fake_az.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "path = pathlib.Path(os.environ['GLUDD_FAKE_AZ_LOG'])\n"
        "with path.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'clientId': 'fake-client'}))\n",
        encoding="utf-8",
    )
    fake_az.chmod(0o700)
    environment = {**os.environ, "GLUDD_FAKE_AZ_LOG": str(invocation_log)}

    for arguments in (
        subject.build_role_arguments(subscription_id=SUBSCRIPTION_ID),
        AUTH_ARGS,
    ):
        result = subprocess.run(
            ["xargs", "-0", str(fake_az)],
            input=b"\0".join(value.encode("utf-8") for value in arguments) + b"\0",
            capture_output=True,
            timeout=10,
            env=environment,
        )
        assert result.returncode == 0
        assert result.stderr == b""

    invocations = [json.loads(line) for line in invocation_log.read_text().splitlines()]
    assert invocations == [
        list(subject.build_role_arguments(subscription_id=SUBSCRIPTION_ID)),
        list(AUTH_ARGS),
    ]


def test_help_contract_docs_and_gitignore_pin_accelerator_workflow() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    docs = (ROOT / "docs/azure-iam-setup.md").read_text(encoding="utf-8")
    iam_docs = (ROOT / "config/infra/IAM_README.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    entries = {item["name"]: item for item in contract["targets"]}

    assert "azure-accelerator-role-args" in makefile
    assert "azure-accelerator-auth-args" in makefile
    assert entries["azure-accelerator-role-args"]["make_variables"] == [
        "AZURE_ACCELERATOR_SUBSCRIPTION_ID"
    ]
    assert entries["azure-accelerator-auth-args"]["make_variables"] == [
        "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
        "AZURE_ACCELERATOR_SP_NAME",
    ]
    role_command = (
        "make --no-print-directory azure-accelerator-role-args "
        "AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 "
        "| xargs -0 az"
    )
    assert role_command in docs
    for guide in (docs, iam_docs):
        assert "Microsoft.Authorization/roleDefinitions/write" in guide
        assert 'role "User Access Administrator"' in guide
        assert "az role assignment delete --assignee-object-id" in guide
    assert "azure-accelerator-role-args" in iam_docs
    assert "azure-accelerator-auth-args" in iam_docs
    assert "gludd-azure-accelerator-auth.*" in gitignore


def test_renderer_and_make_targets_never_receive_a_credential() -> None:
    script = Path(subject.__file__).read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    role_target = makefile.split("azure-accelerator-role-args:", 1)[1].split("\n\n", 1)[0]
    auth_target = makefile.split("azure-accelerator-auth-args:", 1)[1].split("\n\n", 1)[0]

    assert "CLIENT_SECRET" not in script
    assert "password" not in script.casefold()
    assert "subprocess" not in script
    assert "CLIENT_SECRET" not in role_target
    assert "CLIENT_SECRET" not in auth_target
