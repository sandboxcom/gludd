"""Hermetic contracts for Azure self-improvement credential arguments."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import render_azure_self_improve_auth_args as subject

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models"
ACCOUNT = "gludd-self-improve"
SP_NAME = "gludd self improve 20260905"
SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}"
)
ROLE_ASSIGNABLE_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
)
ROLE_NAME = f"Gludd Azure OpenAI Self Improvement - {ACCOUNT}"
EXPECTED_ARGS = (
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


def _kwargs(**overrides: str) -> dict[str, str]:
    values = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": RESOURCE_GROUP,
        "account": ACCOUNT,
        "service_principal_name": SP_NAME,
    }
    values.update(overrides)
    return values


def _argv(**overrides: str) -> list[str]:
    values = {
        "subscription-id": SUBSCRIPTION_ID,
        "resource-group": RESOURCE_GROUP,
        "account": ACCOUNT,
        "service-principal-name": SP_NAME,
    }
    values.update(overrides)
    return [item for key, value in values.items() for item in (f"--{key}", value)]


def test_arguments_are_byte_exact_nul_delimited_and_account_scoped() -> None:
    payload = subject.render_arguments(**_kwargs())

    assert payload == b"\0".join(argument.encode("utf-8") for argument in EXPECTED_ARGS) + b"\0"
    assert payload.split(b"\0") == [
        *(argument.encode("utf-8") for argument in EXPECTED_ARGS),
        b"",
    ]


def test_arguments_create_one_new_principal_and_one_exact_role_assignment() -> None:
    arguments = subject.build_arguments(**_kwargs())

    assert arguments == EXPECTED_ARGS
    assert "--skip-assignment" not in arguments
    assert arguments.count("--role") == 1
    assert arguments.count("--scopes") == 1
    assert arguments[arguments.index("--scopes") + 1] == SCOPE
    assert arguments[arguments.index("--role") + 1] == ROLE_NAME
    assert "--json-auth" in arguments
    assert "--only-show-errors" in arguments


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("subscription_id", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),
        ("resource_group", "../escape"),
        ("resource_group", "ends-in-dot."),
        ("account", "Uppercase"),
        ("account", "-starts-with-dash"),
        ("account", "x"),
        ("account", "x" * 65),
        ("service_principal_name", "--subscription"),
        ("service_principal_name", " leading-space"),
        ("service_principal_name", "contains\0nul"),
        ("service_principal_name", "x" * 121),
    ],
)
def test_invalid_or_option_shaped_input_is_rejected_before_output(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        subject.render_arguments(**_kwargs(**{field: value}))


def test_spaces_are_data_not_azure_cli_arguments() -> None:
    name = "gludd self improvement ci"
    payload = subject.render_arguments(**_kwargs(service_principal_name=name))
    fields = payload.removesuffix(b"\0").split(b"\0")

    assert fields[fields.index(b"--name") + 1] == name.encode("utf-8")
    assert b"gludd" not in fields
    assert b"ci" not in fields


def test_main_stdout_is_only_nul_delimited_arguments(
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    assert subject.main(_argv()) == 0
    captured = capsysbinary.readouterr()

    assert captured.out == b"\0".join(argument.encode() for argument in EXPECTED_ARGS) + b"\0"
    assert captured.err == b""


def test_main_redacts_invalid_input_from_errors(
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    invalid = "--sensitive-looking-option"
    assert subject.main(_argv(**{"service-principal-name": invalid})) == 2
    captured = capsysbinary.readouterr()

    assert captured.out == b""
    assert invalid.encode() not in captured.err
    assert captured.err == b"azure-self-improve-auth-args: invalid service principal name\n"


def test_main_reads_make_exported_values_without_a_shell_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    environment = {
        "_GLUDD_AZURE_SELF_IMPROVE_SUBSCRIPTION_ID_RAW": SUBSCRIPTION_ID,
        "_GLUDD_AZURE_SELF_IMPROVE_RESOURCE_GROUP_RAW": RESOURCE_GROUP,
        "_GLUDD_AZURE_SELF_IMPROVE_ACCOUNT_RAW": ACCOUNT,
        "_GLUDD_AZURE_SELF_IMPROVE_SP_NAME_RAW": SP_NAME,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert subject.main([]) == 0

    captured = capsysbinary.readouterr()
    assert captured.out == subject.render_arguments(**_kwargs())
    assert captured.err == b""


def test_missing_make_export_fails_before_any_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    names = (
        "_GLUDD_AZURE_SELF_IMPROVE_SUBSCRIPTION_ID_RAW",
        "_GLUDD_AZURE_SELF_IMPROVE_RESOURCE_GROUP_RAW",
        "_GLUDD_AZURE_SELF_IMPROVE_ACCOUNT_RAW",
        "_GLUDD_AZURE_SELF_IMPROVE_SP_NAME_RAW",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    assert subject.main([]) == 2

    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert captured.err == (
        b"azure-self-improve-auth-args: "
        b"AZURE_SELF_IMPROVE_SUBSCRIPTION_ID is required\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--account"],
        [
            "--unknown",
            "value",
            "--resource-group",
            RESOURCE_GROUP,
            "--account",
            ACCOUNT,
            "--service-principal-name",
            SP_NAME,
        ],
        [
            "--account",
            ACCOUNT,
            "--account",
            ACCOUNT,
            "--subscription-id",
            SUBSCRIPTION_ID,
            "--resource-group",
            RESOURCE_GROUP,
        ],
    ],
)
def test_argument_parser_rejects_incomplete_unknown_or_duplicate_fields(argv: list[str]) -> None:
    with pytest.raises(ValueError, match="invalid arguments"):
        subject._parse(argv)


def test_make_target_stdout_is_byte_exact_and_stderr_clean() -> None:
    result = subprocess.run(
        [
            "make",
            "azure-self-improve-auth-args",
            f"AZURE_SELF_IMPROVE_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_SELF_IMPROVE_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_SELF_IMPROVE_ACCOUNT={ACCOUNT}",
            f"AZURE_SELF_IMPROVE_SP_NAME={SP_NAME}",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == b"\0".join(argument.encode() for argument in EXPECTED_ARGS) + b"\0"
    assert result.stderr == b""


def test_nul_stream_round_trips_through_one_fake_azure_cli_process(tmp_path: Path) -> None:
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
    producer = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-self-improve-auth-args",
            f"AZURE_SELF_IMPROVE_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_SELF_IMPROVE_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_SELF_IMPROVE_ACCOUNT={ACCOUNT}",
            f"AZURE_SELF_IMPROVE_SP_NAME={SP_NAME}",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    consumer = subprocess.run(
        ["xargs", "-0", str(fake_az)],
        input=producer.stdout,
        capture_output=True,
        timeout=10,
        env={**os.environ, "GLUDD_FAKE_AZ_LOG": str(invocation_log)},
    )

    assert producer.returncode == 0
    assert consumer.returncode == 0
    assert consumer.stderr == b""
    assert json.loads(consumer.stdout)["clientId"] == "fake-client"
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert len(invocations) == 1
    assert json.loads(invocations[0]) == list(EXPECTED_ARGS)


def test_make_target_missing_input_has_no_partial_argument_stream() -> None:
    result = subprocess.run(
        [
            "make",
            "azure-self-improve-auth-args",
            f"AZURE_SELF_IMPROVE_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_SELF_IMPROVE_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_SELF_IMPROVE_ACCOUNT={ACCOUNT}",
            "AZURE_SELF_IMPROVE_SP_NAME=",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.startswith(
        b"azure-self-improve-auth-args: AZURE_SELF_IMPROVE_SP_NAME is required\n"
    )
    assert result.stderr.endswith(b"[azure-self-improve-auth-args] Error 2\n")


def test_make_function_shaped_input_is_never_evaluated(tmp_path: Path) -> None:
    sentinel = tmp_path / "must-not-exist"
    malicious = f"$(shell /usr/bin/touch {sentinel})"
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-self-improve-auth-args",
            f"AZURE_SELF_IMPROVE_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_SELF_IMPROVE_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_SELF_IMPROVE_ACCOUNT={ACCOUNT}",
            f"AZURE_SELF_IMPROVE_SP_NAME={malicious}",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == b""
    assert not sentinel.exists()
    assert malicious.encode() not in result.stderr


def test_help_contract_docs_and_gitignore_pin_the_one_azure_call_workflow() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    docs = (ROOT / "docs/azure-iam-setup.md").read_text(encoding="utf-8")
    normalized_docs = " ".join(docs.split())
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    entry = next(item for item in contract["targets"] if item["name"] == "azure-self-improve-auth-args")

    assert "azure-self-improve-auth-args" in makefile
    assert entry["make_variables"] == [
        "AZURE_SELF_IMPROVE_SUBSCRIPTION_ID",
        "AZURE_SELF_IMPROVE_RESOURCE_GROUP",
        "AZURE_SELF_IMPROVE_ACCOUNT",
        "AZURE_SELF_IMPROVE_SP_NAME",
    ]
    assert entry["behavior"].startswith("make azure-self-improve-auth-args ")
    exact = (
        "(umask 077; make --no-print-directory azure-self-improve-auth-args "
        "AZURE_SELF_IMPROVE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 "
        "AZURE_SELF_IMPROVE_RESOURCE_GROUP=gludd-models "
        "AZURE_SELF_IMPROVE_ACCOUNT=gludd-self-improve "
        "AZURE_SELF_IMPROVE_SP_NAME=gludd-self-improve-20260905 "
        "| xargs -0 az > /tmp/gludd-azure-self-improve-auth.json)"
    )
    assert exact in docs
    assert "cannot create or update a custom role" in docs
    assert "ignores stdin" in docs
    assert "#31995" in docs
    assert "#31579" in docs
    assert "responses/write" in docs
    assert "management-group or subscription-level resource" in normalized_docs
    assert "custom role is assignable at the exact resource-group scope" in normalized_docs
    assert "role assignment remains narrowed to the exact account resource" in normalized_docs
    assert "[Azure custom-role scope rules][azure-custom-role-scope]" in docs
    assert "Gludd maps `clientId`" not in docs
    assert "The operator or secret-injection workflow must read" in normalized_docs
    assert "gludd-azure-self-improve-auth.*" in gitignore


def test_checked_in_custom_role_template_matches_the_emitted_role_and_scope() -> None:
    role = json.loads(
        (ROOT / "config/infra/azure-self-improve-role.json").read_text(encoding="utf-8")
    )

    assert role["Name"] == "Gludd Azure OpenAI Self Improvement - {account_name}"
    assert role["Actions"] == [
        "Microsoft.CognitiveServices/accounts/read",
        "Microsoft.CognitiveServices/accounts/deployments/read",
    ]
    assert role["DataActions"] == [
        "Microsoft.CognitiveServices/accounts/OpenAI/responses/write"
    ]
    assert role["NotActions"] == []
    assert role["NotDataActions"] == []
    assert role["AssignableScopes"] == [
        "/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    ]
    assert (
        role["AssignableScopes"][0].format(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
        )
        == ROLE_ASSIGNABLE_SCOPE
    )
    assert SCOPE.startswith(f"{ROLE_ASSIGNABLE_SCOPE}/providers/")


def test_target_and_renderer_never_contain_or_accept_a_credential() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    script = (ROOT / "scripts/render_azure_self_improve_auth_args.py").read_text(encoding="utf-8")
    target_body = makefile.split("azure-self-improve-auth-args:", 1)[1].split("\n\n", 1)[0]

    assert "CLIENT_SECRET" not in target_body
    assert "password" not in target_body.casefold()
    assert "CLIENT_SECRET" not in script
    assert "password" not in script.casefold()
    assert "subprocess" not in script
