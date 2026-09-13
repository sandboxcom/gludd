"""Apply Gludd's bounded Azure accelerator role with Microsoft SDKs.

The checked-in role document is the single source for the legacy CLI renderer,
Terraform parity tests, and the SDK operator path.  Runtime credentials cannot
call this module: applying a role is an explicit operator-only bootstrap step.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

from general_ludd.azure.rbac_validator import validate_against_azure_schema

ROLE_NAME: Final = "General Ludd Accelerator Deployer"
ROLE_DEFINITION_ID: Final = "96008390-cad3-42f5-b72a-b5230b176675"
ROLE_TEMPLATE_PATH: Final = (
    Path(__file__).resolve().parents[3] / "config" / "infra" / "azure-iam-policy.json"
)
ROLE_SCOPE_TEMPLATE: Final = (
    "/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
)
EXPECTED_ACTIONS: Final = frozenset(
    {
        "Microsoft.App/managedEnvironments/read",
        "Microsoft.App/managedEnvironments/write",
        "Microsoft.App/managedEnvironments/delete",
        "Microsoft.App/managedEnvironments/join/action",
        "Microsoft.App/managedEnvironments/usages/read",
        "Microsoft.App/managedEnvironments/workloadProfileStates/read",
        "Microsoft.App/containerApps/read",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/containerApps/delete",
        "Microsoft.App/containerApps/revisions/read",
        "Microsoft.App/locations/containerAppOperationResults/read",
        "Microsoft.App/locations/containerAppOperationStatuses/read",
        "Microsoft.App/locations/managedEnvironmentOperationResults/read",
        "Microsoft.App/locations/managedEnvironmentOperationStatuses/read",
        "Microsoft.Insights/metricDefinitions/read",
        "Microsoft.Insights/metrics/read",
        "Microsoft.Resources/subscriptions/resourceGroups/read",
        "Microsoft.Resources/subscriptions/resourceGroups/write",
    }
)
_UUID_RE: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_RESOURCE_GROUP_RE: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._()\-]{0,88}[A-Za-z0-9_()\-]?$"
)
_LOCATION_RE: Final = re.compile(r"^[a-z][a-z0-9]{1,31}$")
_RESOURCE_GROUP_TAGS: Final = {
    "gludd-managed-by": "general-ludd",
    "gludd-purpose": "accelerator-boundary",
}
_ROLE_FIELDS: Final = frozenset(
    {
        "Name",
        "Description",
        "Actions",
        "NotActions",
        "DataActions",
        "NotDataActions",
        "AssignableScopes",
    }
)


AzureAcceleratorFailureClass = Literal[
    "authentication",
    "authorization",
    "cleanup",
    "conflict",
    "dependency",
    "internal",
    "not-found",
    "provider",
    "quota",
    "sdk-contract",
    "validation",
]
AzureAcceleratorOperation = Literal[
    "authorization-client",
    "check",
    "cleanup",
    "create",
    "credential",
    "resource-client",
    "role-assignment",
    "role-definition",
]


class AzureAcceleratorRoleError(RuntimeError):
    """A censored failure from the operator-owned SDK apply boundary."""

    def __init__(self, failure_class: AzureAcceleratorFailureClass) -> None:
        """Initialize a failure without retaining provider-controlled text."""
        self.failure_class = failure_class
        super().__init__("Azure accelerator role apply failed")


class AzureAcceleratorDependencyError(RuntimeError):
    """The locked Microsoft Azure management SDK is unavailable."""

    def __init__(self) -> None:
        """Initialize the fixed dependency error."""
        super().__init__("Azure accelerator SDK dependency unavailable")


@dataclass(frozen=True, slots=True)
class AcceleratorRoleTrace:
    """Content-free progress for one role-definition operation."""

    phase: Literal["resource_group", "role_definition"]
    state: Literal["validated", "started", "applied", "failed"]
    assignment_requested: bool
    failure_class: AzureAcceleratorFailureClass | None = None
    operation: AzureAcceleratorOperation | None = None


@dataclass(frozen=True, slots=True)
class AcceleratorRoleApplyResult:
    """Non-sensitive result from one validation or live apply."""

    state: Literal["validated", "applied"]
    action_count: int
    assignment_created: bool
    resource_group_created: bool = False


RoleTraceSink = Callable[[AcceleratorRoleTrace], None]


def _discard_trace(_event: AcceleratorRoleTrace) -> None:
    return None


def validate_subscription_id(value: object) -> str:
    """Return a canonical Azure subscription UUID."""
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError("invalid subscription ID for Azure accelerator")
    return value


def validate_resource_group(value: object) -> str:
    """Return a bounded Azure resource-group name."""
    if not isinstance(value, str) or _RESOURCE_GROUP_RE.fullmatch(value) is None:
        raise ValueError("invalid resource group for Azure accelerator")
    return value


def _validate_principal_object_id(value: object) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError("invalid Azure accelerator principal object ID")
    return value


def resource_group_scope(*, subscription_id: str, resource_group: str) -> str:
    """Return the one exact assignment and assignable scope."""
    subscription_id = validate_subscription_id(subscription_id)
    resource_group = validate_resource_group(resource_group)
    return f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"


def materialize_accelerator_role(
    *,
    subscription_id: str,
    resource_group: str,
    template_path: Path = ROLE_TEMPLATE_PATH,
) -> dict[str, Any]:
    """Validate and scope the canonical accelerator role document."""
    scope = resource_group_scope(
        subscription_id=subscription_id,
        resource_group=resource_group,
    )
    try:
        raw = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid accelerator role template for Azure") from exc
    if not isinstance(raw, dict):
        raise ValueError("invalid accelerator role template for Azure")
    role = cast(dict[str, Any], raw)
    actions = role.get("Actions")
    if (
        frozenset(role) != _ROLE_FIELDS
        or role.get("Name") != ROLE_NAME
        or not isinstance(role.get("Description"), str)
        or not isinstance(actions, list)
        or any(not isinstance(action, str) for action in actions)
        or len(actions) != len(EXPECTED_ACTIONS)
        or frozenset(actions) != EXPECTED_ACTIONS
        or role.get("NotActions") != []
        or role.get("DataActions") != []
        or role.get("NotDataActions") != []
        or role.get("AssignableScopes") != [ROLE_SCOPE_TEMPLATE]
    ):
        raise ValueError("invalid accelerator role template for Azure")
    scoped = dict(role)
    scoped["AssignableScopes"] = [scope]
    valid, messages = validate_against_azure_schema(scoped)
    if not valid or messages:
        raise ValueError("invalid accelerator role template for Azure")
    return scoped


def role_assignment_id(
    *,
    principal_object_id: str,
    assignment_scope: str,
) -> str:
    """Return a stable assignment UUID for the exact principal and scope."""
    principal = _validate_principal_object_id(principal_object_id)
    seed = (
        "gludd-azure-accelerator-assignment\0"
        f"{principal}\0{assignment_scope}\0{ROLE_DEFINITION_ID}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _build_operator_credential(operator_auth: str, subscription_id: str) -> Any:
    """Build one explicit non-interactive Azure Identity credential."""
    if operator_auth not in {"cli", "environment"}:
        raise ValueError("operator_auth must be cli or environment")
    try:
        from azure.identity import AzureCliCredential, EnvironmentCredential
    except ImportError as exc:
        raise AzureAcceleratorDependencyError from exc
    if operator_auth == "cli":
        return AzureCliCredential(subscription=subscription_id)
    if operator_auth == "environment":
        return EnvironmentCredential()
    raise AssertionError("validated operator auth was not handled")


def _build_authorization_client(credential: Any, subscription_id: str) -> Any:
    """Build Microsoft's supported Authorization management client."""
    try:
        from azure.mgmt.authorization import AuthorizationManagementClient
    except ImportError as exc:
        raise AzureAcceleratorDependencyError from exc
    return AuthorizationManagementClient(
        credential=credential,
        subscription_id=subscription_id,
    )


def _build_resource_management_client(credential: Any, subscription_id: str) -> Any:
    """Build Microsoft's supported resource-group management client."""
    try:
        from azure.mgmt.resource.resources import ResourceManagementClient
    except ImportError as exc:
        raise AzureAcceleratorDependencyError from exc
    return ResourceManagementClient(
        credential=credential,
        subscription_id=subscription_id,
    )


def _build_role_definition_model(role: Mapping[str, Any]) -> Any:
    """Translate the checked-in role into official Azure SDK models."""
    try:
        from azure.mgmt.authorization.models import Permission, RoleDefinition
    except ImportError as exc:
        raise AzureAcceleratorDependencyError from exc
    permission = Permission(
        actions=role["Actions"],
        not_actions=role["NotActions"],
        data_actions=role["DataActions"],
        not_data_actions=role["NotDataActions"],
    )
    return RoleDefinition(
        role_name=role["Name"],
        description=role["Description"],
        permissions=[permission],
        assignable_scopes=role["AssignableScopes"],
    )


def _build_role_assignment_model(
    *,
    principal_object_id: str,
    role_definition_resource_id: str,
) -> Any:
    """Build one service-principal assignment model."""
    try:
        from azure.mgmt.authorization.v2022_04_01.models import (
            RoleAssignmentCreateParameters,
        )
    except ImportError as exc:
        raise AzureAcceleratorDependencyError from exc
    return RoleAssignmentCreateParameters(
        principal_id=principal_object_id,
        principal_type="ServicePrincipal",
        role_definition_id=role_definition_resource_id,
    )


def _close_sdk_object(value: object | None) -> bool:
    if value is None:
        return True
    close = getattr(value, "close", None)
    if not callable(close):
        return True
    try:
        close()
    except Exception:
        return False
    return True


def _classify_sdk_failure(exc: Exception) -> AzureAcceleratorFailureClass:
    """Map SDK metadata to one fixed class without reading provider messages."""
    if isinstance(exc, AzureAcceleratorDependencyError):
        return "dependency"
    if isinstance(exc, (AttributeError, TypeError)):
        return "sdk-contract"
    try:
        status_code = getattr(exc, "status_code", None)
    except Exception:
        status_code = None
    if not isinstance(status_code, int):
        try:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
        except Exception:
            status_code = None
    try:
        error_code = getattr(getattr(exc, "error", None), "code", None)
    except Exception:
        error_code = None
    normalized_code = error_code.casefold() if isinstance(error_code, str) else ""
    exception_type = type(exc).__name__.casefold()
    if "authentication" in exception_type or "credential" in exception_type:
        return "authentication"
    if status_code == 401 or "authentication" in normalized_code:
        return "authentication"
    if status_code == 403 or "authorization" in normalized_code or "forbidden" in normalized_code:
        return "authorization"
    if status_code == 404 or "notfound" in normalized_code:
        return "not-found"
    if status_code == 409 or "conflict" in normalized_code or "alreadyexists" in normalized_code:
        return "conflict"
    if status_code in {400, 422} or "invalid" in normalized_code:
        return "validation"
    if status_code == 429 or "quota" in normalized_code or "throttl" in normalized_code:
        return "quota"
    if "provider" in normalized_code or "registration" in normalized_code:
        return "provider"
    return "internal"


def apply_accelerator_role(
    *,
    subscription_id: str,
    resource_group: str,
    resource_group_location: str | None = None,
    operator_auth: str,
    principal_object_id: str | None,
    live: bool,
    trace_sink: RoleTraceSink = _discard_trace,
) -> AcceleratorRoleApplyResult:
    """Validate or idempotently apply the role and one optional assignment."""
    role = materialize_accelerator_role(
        subscription_id=subscription_id,
        resource_group=resource_group,
    )
    if resource_group_location is not None and (
        not isinstance(resource_group_location, str)
        or _LOCATION_RE.fullmatch(resource_group_location) is None
    ):
        raise ValueError("invalid Azure accelerator resource-group location")
    scope = cast(list[str], role["AssignableScopes"])[0]
    if principal_object_id is not None:
        principal_object_id = _validate_principal_object_id(principal_object_id)
    assignment_requested = principal_object_id is not None
    if not live:
        if resource_group_location is not None:
            trace_sink(
                AcceleratorRoleTrace(
                    phase="resource_group",
                    state="validated",
                    assignment_requested=assignment_requested,
                )
            )
        trace_sink(
            AcceleratorRoleTrace(
                phase="role_definition",
                state="validated",
                assignment_requested=assignment_requested,
            )
        )
        return AcceleratorRoleApplyResult(
            state="validated",
            action_count=len(EXPECTED_ACTIONS),
            assignment_created=False,
        )

    credential: object | None = None
    client: Any = None
    resource_client: Any = None
    failure_class: AzureAcceleratorFailureClass | None = None
    active_phase: Literal["resource_group", "role_definition"] = (
        "resource_group"
        if resource_group_location is not None
        else "role_definition"
    )
    active_operation: AzureAcceleratorOperation = "credential"
    resource_group_created = False
    try:
        credential = _build_operator_credential(operator_auth, subscription_id)
        if resource_group_location is not None:
            trace_sink(
                AcceleratorRoleTrace(
                    phase="resource_group",
                    state="started",
                    assignment_requested=assignment_requested,
                )
            )
            active_operation = "resource-client"
            resource_client = _build_resource_management_client(
                credential,
                subscription_id,
            )
            active_operation = "check"
            exists = resource_client.resource_groups.check_existence(resource_group)
            if not isinstance(exists, bool):
                raise TypeError("invalid resource-group existence result")
            if not exists:
                active_operation = "create"
                resource_client.resource_groups.create_or_update(
                    resource_group,
                    {
                        "location": resource_group_location,
                        "tags": dict(_RESOURCE_GROUP_TAGS),
                    },
                )
                resource_group_created = True
            trace_sink(
                AcceleratorRoleTrace(
                    phase="resource_group",
                    state="applied",
                    assignment_requested=assignment_requested,
                )
            )
        active_phase = "role_definition"
        trace_sink(
            AcceleratorRoleTrace(
                phase="role_definition",
                state="started",
                assignment_requested=assignment_requested,
            )
        )
        active_operation = "authorization-client"
        client = _build_authorization_client(credential, subscription_id)
        definition_scope = f"/subscriptions/{subscription_id}"
        active_operation = "role-definition"
        client.role_definitions.create_or_update(
            scope=definition_scope,
            role_definition_id=ROLE_DEFINITION_ID,
            role_definition=_build_role_definition_model(role),
        )
        if principal_object_id is not None:
            active_operation = "role-assignment"
            definition_resource_id = (
                f"{definition_scope}/providers/Microsoft.Authorization/"
                f"roleDefinitions/{ROLE_DEFINITION_ID}"
            )
            client.role_assignments.create(
                scope=scope,
                role_assignment_name=role_assignment_id(
                    principal_object_id=principal_object_id,
                    assignment_scope=scope,
                ),
                parameters=_build_role_assignment_model(
                    principal_object_id=principal_object_id,
                    role_definition_resource_id=definition_resource_id,
                ),
            )
    except Exception as exc:
        failure_class = _classify_sdk_failure(exc)
    client_closed = _close_sdk_object(client)
    resource_client_closed = _close_sdk_object(resource_client)
    credential_closed = _close_sdk_object(credential)
    cleanup_ok = client_closed and resource_client_closed and credential_closed
    if failure_class is not None or not cleanup_ok:
        failure_class = failure_class or "cleanup"
        if failure_class == "cleanup":
            active_operation = "cleanup"
        trace_sink(
            AcceleratorRoleTrace(
                phase=active_phase,
                state="failed",
                assignment_requested=assignment_requested,
                failure_class=failure_class,
                operation=active_operation,
            )
        )
        raise AzureAcceleratorRoleError(failure_class)
    trace_sink(
        AcceleratorRoleTrace(
            phase="role_definition",
            state="applied",
            assignment_requested=assignment_requested,
        )
    )
    return AcceleratorRoleApplyResult(
        state="applied",
        action_count=len(EXPECTED_ACTIONS),
        assignment_created=assignment_requested,
        resource_group_created=resource_group_created,
    )


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError("required Azure accelerator role input is missing")
    return value


@contextmanager
def _censor_sdk_logging() -> Iterator[None]:
    """Prevent provider-controlled log records from crossing the CLI boundary."""
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous_disable)


def _print_trace(event: AcceleratorRoleTrace) -> None:
    print(
        "AZURE_ACCELERATOR_ROLE_TRACE "
        f"phase={event.phase} state={event.state} "
        f"assignment_requested={str(event.assignment_requested).lower()} "
        f"failure_class={event.failure_class or 'none'} "
        f"operation={event.operation or 'none'} "
        "secret_output=false",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Make-facing, content-free entry point for role validation/application."""
    if list(sys.argv[1:] if argv is None else argv):
        print(
            "AZURE_ACCELERATOR_ROLE_INVALID reason=arguments secret_output=false",
            file=sys.stderr,
        )
        return 2
    try:
        live_raw = _required_environment(
            "_GLUDD_AZURE_ACCELERATOR_ROLE_APPLY_LIVE_RAW"
        )
        if live_raw not in {"0", "1"}:
            raise ValueError("invalid live mode")
        principal = os.environ.get(
            "_GLUDD_AZURE_ACCELERATOR_PRINCIPAL_OBJECT_ID_RAW",
        ) or None
        with _censor_sdk_logging():
            result = apply_accelerator_role(
                subscription_id=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_SUBSCRIPTION_ID_RAW"
                ),
                resource_group=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_RESOURCE_GROUP_RAW"
                ),
                resource_group_location=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_LOCATION_RAW"
                ),
                operator_auth=_required_environment(
                    "_GLUDD_AZURE_ACCELERATOR_OPERATOR_AUTH_RAW"
                ),
                principal_object_id=principal,
                live=live_raw == "1",
                trace_sink=_print_trace,
            )
    except ValueError:
        print(
            "AZURE_ACCELERATOR_ROLE_INVALID reason=configuration secret_output=false",
            file=sys.stderr,
        )
        return 2
    except AzureAcceleratorRoleError as exc:
        print(
            "AZURE_ACCELERATOR_ROLE_INVALID reason=apply "
            f"failure_class={exc.failure_class} secret_output=false",
            file=sys.stderr,
        )
        return 2
    print(
        "AZURE_ACCELERATOR_ROLE_RESULT "
        f"state={result.state} action_count={result.action_count} "
        f"assignment_created={str(result.assignment_created).lower()} "
        f"resource_group_created={str(result.resource_group_created).lower()} "
        "secret_output=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "EXPECTED_ACTIONS",
    "ROLE_DEFINITION_ID",
    "ROLE_NAME",
    "ROLE_SCOPE_TEMPLATE",
    "ROLE_TEMPLATE_PATH",
    "AcceleratorRoleApplyResult",
    "AcceleratorRoleTrace",
    "AzureAcceleratorDependencyError",
    "AzureAcceleratorRoleError",
    "apply_accelerator_role",
    "main",
    "materialize_accelerator_role",
    "resource_group_scope",
    "role_assignment_id",
    "validate_resource_group",
    "validate_subscription_id",
]
