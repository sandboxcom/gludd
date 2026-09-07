"""Contracts for SDK-owned Azure accelerator role application."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.azure import accelerator_role as subject

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
RESOURCE_GROUP_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
)
SUBSCRIPTION_SCOPE = f"/subscriptions/{SUBSCRIPTION_ID}"
PRINCIPAL_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LOCATION = "eastus"
ROOT = Path(__file__).resolve().parents[2]


def test_materialized_role_is_the_exact_checked_in_resource_group_policy() -> None:
    role = subject.materialize_accelerator_role(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
    )

    assert role["Name"] == subject.ROLE_NAME
    assert role["AssignableScopes"] == [RESOURCE_GROUP_SCOPE]
    assert frozenset(role["Actions"]) == subject.EXPECTED_ACTIONS
    assert len(role["Actions"]) == 15
    assert role["NotActions"] == []
    assert role["DataActions"] == []
    assert role["NotDataActions"] == []


@pytest.mark.parametrize(
    ("subscription_id", "resource_group"),
    [
        ("not-a-uuid", RESOURCE_GROUP),
        ("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", RESOURCE_GROUP),
        (SUBSCRIPTION_ID, "--subscription"),
        (SUBSCRIPTION_ID, "unsafe group"),
    ],
)
def test_materialization_rejects_noncanonical_scope_inputs(
    subscription_id: str,
    resource_group: str,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        subject.materialize_accelerator_role(
            subscription_id=subscription_id,
            resource_group=resource_group,
        )


def test_materialization_fails_closed_on_policy_drift(tmp_path: Path) -> None:
    policy = json.loads(subject.ROLE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    policy["Actions"].append("Microsoft.Authorization/roleAssignments/write")
    path = tmp_path / "role.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid accelerator role template"):
        subject.materialize_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            template_path=path,
        )


def test_validate_only_constructs_no_credential_or_cloud_client() -> None:
    traces: list[subject.AcceleratorRoleTrace] = []

    with patch.object(
        subject,
        "_build_operator_credential",
        side_effect=AssertionError("credential construction is forbidden"),
    ):
        result = subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            operator_auth="cli",
            principal_object_id=None,
            live=False,
            trace_sink=traces.append,
        )

    assert result == subject.AcceleratorRoleApplyResult(
        state="validated",
        action_count=15,
        assignment_created=False,
    )
    assert traces == [
        subject.AcceleratorRoleTrace(
            phase="role_definition",
            state="validated",
            assignment_requested=False,
        )
    ]
    assert SUBSCRIPTION_ID not in repr(result) + repr(traces)


def test_live_apply_uses_sdk_role_and_exact_optional_assignment() -> None:
    credential = MagicMock(name="credential")
    client = MagicMock(name="authorization_client")
    role_model = object()
    assignment_model = object()
    traces: list[subject.AcceleratorRoleTrace] = []

    with (
        patch.object(subject, "_build_operator_credential", return_value=credential) as build_credential,
        patch.object(subject, "_build_authorization_client", return_value=client) as build_client,
        patch.object(subject, "_build_role_definition_model", return_value=role_model) as build_role,
        patch.object(subject, "_build_role_assignment_model", return_value=assignment_model) as build_assignment,
    ):
        result = subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            operator_auth="cli",
            principal_object_id=PRINCIPAL_ID,
            live=True,
            trace_sink=traces.append,
        )

    build_credential.assert_called_once_with("cli", SUBSCRIPTION_ID)
    build_client.assert_called_once_with(credential, SUBSCRIPTION_ID)
    role = subject.materialize_accelerator_role(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
    )
    build_role.assert_called_once_with(role)
    client.role_definitions.create_or_update.assert_called_once_with(
        scope=SUBSCRIPTION_SCOPE,
        role_definition_id=subject.ROLE_DEFINITION_ID,
        role_definition=role_model,
    )
    role_definition_resource_id = (
        f"{SUBSCRIPTION_SCOPE}/providers/Microsoft.Authorization/roleDefinitions/"
        f"{subject.ROLE_DEFINITION_ID}"
    )
    build_assignment.assert_called_once_with(
        principal_object_id=PRINCIPAL_ID,
        role_definition_resource_id=role_definition_resource_id,
    )
    assignment_id = subject.role_assignment_id(
        principal_object_id=PRINCIPAL_ID,
        resource_group_scope=RESOURCE_GROUP_SCOPE,
    )
    client.role_assignments.create.assert_called_once_with(
        scope=RESOURCE_GROUP_SCOPE,
        role_assignment_name=assignment_id,
        parameters=assignment_model,
    )
    client.close.assert_called_once_with()
    credential.close.assert_called_once_with()
    assert result == subject.AcceleratorRoleApplyResult(
        state="applied",
        action_count=15,
        assignment_created=True,
    )
    assert [trace.state for trace in traces] == ["started", "applied"]
    assert PRINCIPAL_ID not in repr(result) + repr(traces)


def test_live_apply_bootstraps_only_the_exact_missing_resource_group() -> None:
    credential = MagicMock(name="credential")
    authorization_client = MagicMock(name="authorization_client")
    resource_client = MagicMock(name="resource_client")
    resource_client.resource_groups.check_existence.return_value = False
    traces: list[subject.AcceleratorRoleTrace] = []

    with (
        patch.object(subject, "_build_operator_credential", return_value=credential),
        patch.object(
            subject,
            "_build_authorization_client",
            return_value=authorization_client,
        ),
        patch.object(
            subject,
            "_build_resource_management_client",
            return_value=resource_client,
            create=True,
        ),
        patch.object(subject, "_build_role_definition_model", return_value=object()),
    ):
        result = subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            resource_group_location=LOCATION,
            operator_auth="cli",
            principal_object_id=None,
            live=True,
            trace_sink=traces.append,
        )

    resource_client.resource_groups.check_existence.assert_called_once_with(
        RESOURCE_GROUP
    )
    resource_client.resource_groups.create_or_update.assert_called_once_with(
        RESOURCE_GROUP,
        {
            "location": LOCATION,
            "tags": {
                "gludd-managed-by": "general-ludd",
                "gludd-purpose": "accelerator-boundary",
            },
        },
    )
    resource_client.close.assert_called_once_with()
    assert result.resource_group_created is True
    assert [(event.phase, event.state) for event in traces] == [
        ("resource_group", "started"),
        ("resource_group", "applied"),
        ("role_definition", "started"),
        ("role_definition", "applied"),
    ]
    assert RESOURCE_GROUP not in repr(result) + repr(traces)


def test_resource_group_failure_is_classified_without_provider_text() -> None:
    class AuthorizationFailure(RuntimeError):
        status_code = 403
        error = SimpleNamespace(code="AuthorizationFailed")

    credential = MagicMock(name="credential")
    resource_client = MagicMock(name="resource_client")
    resource_client.resource_groups.check_existence.return_value = False
    provider_failure = AuthorizationFailure("secret-bearing Azure response")
    resource_client.resource_groups.create_or_update.side_effect = provider_failure
    traces: list[subject.AcceleratorRoleTrace] = []

    with (
        patch.object(subject, "_build_operator_credential", return_value=credential),
        patch.object(
            subject,
            "_build_resource_management_client",
            return_value=resource_client,
        ),
        pytest.raises(subject.AzureAcceleratorRoleError) as raised,
    ):
        subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            resource_group_location=LOCATION,
            operator_auth="cli",
            principal_object_id=None,
            live=True,
            trace_sink=traces.append,
        )

    assert raised.value.failure_class == "authorization"
    assert traces[-1] == subject.AcceleratorRoleTrace(
        phase="resource_group",
        state="failed",
        assignment_requested=False,
        failure_class="authorization",
        operation="create",
    )
    assert "secret-bearing Azure response" not in repr(raised.value) + repr(traces)


def test_sdk_failure_classification_reads_only_fixed_metadata() -> None:
    class ClientAuthenticationError(RuntimeError):
        pass

    class ResponseFailure(RuntimeError):
        response = SimpleNamespace(status_code=403)

    assert (
        subject._classify_sdk_failure(ClientAuthenticationError("private"))
        == "authentication"
    )
    assert subject._classify_sdk_failure(ResponseFailure("private")) == "authorization"
    assert (
        subject._classify_sdk_failure(subject.AzureAcceleratorDependencyError())
        == "dependency"
    )
    assert subject._classify_sdk_failure(AttributeError("private")) == "sdk-contract"

    class SDKFailure(RuntimeError):
        def __init__(
            self,
            *,
            status_code: int | None = None,
            error_code: str | None = None,
        ) -> None:
            super().__init__("private")
            self.status_code = status_code
            self.error = SimpleNamespace(code=error_code)

    cases = (
        (SDKFailure(status_code=401), "authentication"),
        (SDKFailure(error_code="Forbidden"), "authorization"),
        (SDKFailure(status_code=404), "not-found"),
        (SDKFailure(error_code="AlreadyExists"), "conflict"),
        (SDKFailure(status_code=422), "validation"),
        (SDKFailure(error_code="QuotaExceeded"), "quota"),
        (SDKFailure(error_code="MissingSubscriptionRegistration"), "provider"),
        (SDKFailure(), "internal"),
    )
    for failure, expected in cases:
        assert subject._classify_sdk_failure(failure) == expected


def test_apply_without_principal_never_creates_an_assignment() -> None:
    credential = MagicMock()
    client = MagicMock()

    with (
        patch.object(subject, "_build_operator_credential", return_value=credential),
        patch.object(subject, "_build_authorization_client", return_value=client),
        patch.object(subject, "_build_role_definition_model", return_value=object()),
    ):
        result = subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            operator_auth="environment",
            principal_object_id=None,
            live=True,
        )

    client.role_assignments.create.assert_not_called()
    assert result.assignment_created is False


def test_apply_closes_sdk_objects_and_emits_censored_failure() -> None:
    credential = MagicMock()
    client = MagicMock()
    client.role_definitions.create_or_update.side_effect = RuntimeError(
        "private Azure response detail"
    )
    traces: list[subject.AcceleratorRoleTrace] = []

    with (
        patch.object(subject, "_build_operator_credential", return_value=credential),
        patch.object(subject, "_build_authorization_client", return_value=client),
        patch.object(subject, "_build_role_definition_model", return_value=object()),
        pytest.raises(subject.AzureAcceleratorRoleError, match="role apply failed"),
    ):
        subject.apply_accelerator_role(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            operator_auth="cli",
            principal_object_id=None,
            live=True,
            trace_sink=traces.append,
        )

    client.close.assert_called_once_with()
    credential.close.assert_called_once_with()
    assert [trace.state for trace in traces] == ["started", "failed"]
    assert "private Azure response detail" not in repr(traces)


def _identity_modules() -> tuple[dict[str, ModuleType], MagicMock, MagicMock]:
    azure = ModuleType("azure")
    identity = ModuleType("azure.identity")
    cli_type = MagicMock(name="AzureCliCredential")
    environment_type = MagicMock(name="EnvironmentCredential")
    identity.__dict__["AzureCliCredential"] = cli_type
    identity.__dict__["EnvironmentCredential"] = environment_type
    return {"azure": azure, "azure.identity": identity}, cli_type, environment_type


def test_operator_auth_is_explicit_and_noninteractive() -> None:
    modules, cli_type, environment_type = _identity_modules()

    with patch.dict(sys.modules, modules):
        assert subject._build_operator_credential("cli", SUBSCRIPTION_ID) is cli_type.return_value
        assert (
            subject._build_operator_credential("environment", SUBSCRIPTION_ID)
            is environment_type.return_value
        )

    cli_type.assert_called_once_with(subscription=SUBSCRIPTION_ID)
    environment_type.assert_called_once_with()


def test_operator_auth_rejects_default_or_interactive_chains() -> None:
    with pytest.raises(ValueError, match="operator_auth"):
        subject._build_operator_credential("default", SUBSCRIPTION_ID)


def test_sdk_model_builders_use_supported_authorization_models() -> None:
    azure = ModuleType("azure")
    mgmt = ModuleType("azure.mgmt")
    authorization = ModuleType("azure.mgmt.authorization")
    models = ModuleType("azure.mgmt.authorization.models")
    authorization_2022 = ModuleType("azure.mgmt.authorization.v2022_04_01")
    assignment_models = ModuleType(
        "azure.mgmt.authorization.v2022_04_01.models"
    )
    permission_type = MagicMock(name="Permission")
    role_type = MagicMock(name="RoleDefinition")
    assignment_type = MagicMock(name="RoleAssignmentCreateParameters")
    models.__dict__.update(
        Permission=permission_type,
        RoleDefinition=role_type,
    )
    assignment_models.__dict__["RoleAssignmentCreateParameters"] = assignment_type
    modules = {
        "azure": azure,
        "azure.mgmt": mgmt,
        "azure.mgmt.authorization": authorization,
        "azure.mgmt.authorization.models": models,
        "azure.mgmt.authorization.v2022_04_01": authorization_2022,
        "azure.mgmt.authorization.v2022_04_01.models": assignment_models,
    }
    role = subject.materialize_accelerator_role(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
    )

    with patch.dict(sys.modules, modules):
        assert subject._build_role_definition_model(role) is role_type.return_value
        assert subject._build_role_assignment_model(
            principal_object_id=PRINCIPAL_ID,
            role_definition_resource_id="role-resource-id",
        ) is assignment_type.return_value

    permission_type.assert_called_once_with(
        actions=role["Actions"],
        not_actions=[],
        data_actions=[],
        not_data_actions=[],
    )
    role_type.assert_called_once_with(
        role_name=subject.ROLE_NAME,
        description=role["Description"],
        permissions=[permission_type.return_value],
        assignable_scopes=[RESOURCE_GROUP_SCOPE],
    )
    assignment_type.assert_called_once_with(
        principal_id=PRINCIPAL_ID,
        principal_type="ServicePrincipal",
        role_definition_id="role-resource-id",
    )


def test_authorization_client_uses_the_official_sdk() -> None:
    azure = ModuleType("azure")
    mgmt = ModuleType("azure.mgmt")
    authorization = ModuleType("azure.mgmt.authorization")
    client_type = MagicMock(name="AuthorizationManagementClient")
    authorization.__dict__["AuthorizationManagementClient"] = client_type

    with patch.dict(
        sys.modules,
        {
            "azure": azure,
            "azure.mgmt": mgmt,
            "azure.mgmt.authorization": authorization,
        },
    ):
        credential = SimpleNamespace()
        result = subject._build_authorization_client(credential, SUBSCRIPTION_ID)

    assert result is client_type.return_value
    client_type.assert_called_once_with(
        credential=credential,
        subscription_id=SUBSCRIPTION_ID,
    )


def test_resource_group_bootstrap_uses_the_official_sdk() -> None:
    azure = ModuleType("azure")
    mgmt = ModuleType("azure.mgmt")
    resource = ModuleType("azure.mgmt.resource")
    resources = ModuleType("azure.mgmt.resource.resources")
    client_type = MagicMock(name="ResourceManagementClient")
    resources.__dict__["ResourceManagementClient"] = client_type

    with patch.dict(
        sys.modules,
        {
            "azure": azure,
            "azure.mgmt": mgmt,
            "azure.mgmt.resource": resource,
            "azure.mgmt.resource.resources": resources,
        },
    ):
        credential = SimpleNamespace()
        result = subject._build_resource_management_client(
            credential,
            SUBSCRIPTION_ID,
        )

    assert result is client_type.return_value
    client_type.assert_called_once_with(
        credential=credential,
        subscription_id=SUBSCRIPTION_ID,
    )


def test_make_entrypoint_validate_only_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
        SUBSCRIPTION_ID,
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
        RESOURCE_GROUP,
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_LOCATION_RAW",
        LOCATION,
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_OPERATOR_AUTH_RAW",
        "cli",
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_ROLE_APPLY_LIVE_RAW",
        "0",
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID_RAW",
        "",
    )

    assert subject.main([]) == 0

    captured = capsys.readouterr()
    assert "state=validated" in captured.out
    assert "action_count=15" in captured.out
    assert "secret_output=false" in captured.out
    assert captured.err == ""
    assert SUBSCRIPTION_ID not in captured.out
    assert RESOURCE_GROUP not in captured.out


def test_make_entrypoint_censors_sdk_logging_and_restores_logging(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW",
        SUBSCRIPTION_ID,
    )
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW",
        RESOURCE_GROUP,
    )
    monkeypatch.setenv("_GLUDD_AZURE_ACCELERATOR_LOCATION_RAW", LOCATION)
    monkeypatch.setenv("_GLUDD_AZURE_ACCELERATOR_OPERATOR_AUTH_RAW", "cli")
    monkeypatch.setenv("_GLUDD_AZURE_ACCELERATOR_ROLE_APPLY_LIVE_RAW", "1")
    monkeypatch.setenv(
        "_GLUDD_AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID_RAW",
        "",
    )
    previous_disable = logging.root.manager.disable

    def fail_with_provider_log(**_kwargs: object) -> None:
        logging.getLogger("azure.identity").error("private provider response")
        raise subject.AzureAcceleratorRoleError("authentication")

    with patch.object(subject, "apply_accelerator_role", side_effect=fail_with_provider_log):
        assert subject.main([]) == 2

    assert "private provider response" not in caplog.text
    assert logging.root.manager.disable == previous_disable


def test_make_target_and_contract_offer_one_sdk_apply_command() -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "azure-accelerator-role-apply",
            f"AZURE_ACCELERATOR_SUBSCRIPTION_ID={SUBSCRIPTION_ID}",
            f"AZURE_ACCELERATOR_RESOURCE_GROUP={RESOURCE_GROUP}",
            f"AZURE_ACCELERATOR_LOCATION={LOCATION}",
            "AZURE_ACCELERATOR_OPERATOR_AUTH=cli",
            "AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID=",
            "AZURE_ACCELERATOR_ROLE_APPLY_LIVE=0",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"state=validated" in result.stdout
    assert SUBSCRIPTION_ID.encode() not in result.stdout + result.stderr
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "azure-accelerator-role-apply:" in makefile
    assert "python -m general_ludd.azure.accelerator_role" in makefile
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "azure-accelerator-role-apply"
    )
    assert entry["make_variables"] == [
        "AZURE_ACCELERATOR_SUBSCRIPTION_ID",
        "AZURE_ACCELERATOR_RESOURCE_GROUP",
        "AZURE_ACCELERATOR_LOCATION",
        "AZURE_ACCELERATOR_OPERATOR_AUTH",
        "AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID",
        "AZURE_ACCELERATOR_ROLE_APPLY_LIVE",
    ]
