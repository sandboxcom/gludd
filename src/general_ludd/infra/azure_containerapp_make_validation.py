"""Filesystem-safe output and ARM evidence validation for the Make runtime."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AzureContainerAppMakeRuntimeError,
)

OWNERSHIP_MARKER = ".gludd-azure-containerapp-live-proof.json"
_OWNERSHIP_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
_MAX_JSON_BYTES = 2 * 1024 * 1024
_OUTPUT_NAMES = frozenset(
    {
        "base_url",
        "cleanup_boundary",
        "endpoint_url",
        "instance_id",
        "instance_ip",
        "revision_name",
        "workload_profile_type",
    }
)


class _DuplicateJSONField(ValueError):
    pass


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONField
        result[key] = value
    return result


def read_bounded_json(path: Path, *, phase: str) -> object:
    """Read one regular, non-symlink, bounded JSON artifact without races."""
    descriptor: int | None = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_JSON_BYTES:
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > _MAX_JSON_BYTES
        ):
            raise ValueError
        chunks: list[bytes] = []
        remaining = _MAX_JSON_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_JSON_BYTES:
            raise ValueError
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise AzureContainerAppMakeRuntimeError(phase) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_ownership_marker(path: Path, operation_digest: str) -> None:
    """Atomically write the exclusive state-ownership marker at mode 0600."""
    payload = json.dumps(
        {
            "operation_digest": operation_digest,
            "protocol": _OWNERSHIP_PROTOCOL,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        temporary.unlink(missing_ok=True)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    except OSError:
        raise AzureContainerAppMakeRuntimeError("ownership-marker") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def mapping(value: object) -> Mapping[str, object]:
    """Narrow one decoded JSON value to an object mapping."""
    if not isinstance(value, Mapping):
        raise ValueError
    return cast(Mapping[str, object], value)


def member(value: object, name: str) -> object:
    """Return one required member from a decoded JSON object."""
    fields = mapping(value)
    if name not in fields:
        raise ValueError
    return fields[name]


def required_argument(arguments: object, marker: str, expected: str) -> None:
    """Require one exact marker/value pair in Terraform plan arguments."""
    if (
        not isinstance(arguments, Sequence)
        or isinstance(arguments, (str, bytes))
        or any(not isinstance(item, str) for item in arguments)
    ):
        raise ValueError
    values = cast(Sequence[str], arguments)
    indexes = [index for index, value in enumerate(values) if value == marker]
    if (
        len(indexes) != 1
        or indexes[0] + 1 >= len(values)
        or values[indexes[0] + 1] != expected
    ):
        raise ValueError


def output_value(payload: Mapping[str, object], name: str) -> str:
    """Return one non-sensitive typed string from Terraform output JSON."""
    output = mapping(payload[name])
    if output.get("sensitive") is not False or output.get("type") != "string":
        raise ValueError
    value = output.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def deployment_outputs(
    payload: object,
    policy: AzureContainerAppLiveProofPolicy,
) -> AzureContainerAppDeploymentEvidence:
    """Bind the exact Terraform output schema to the approved policy."""
    try:
        outputs = mapping(payload)
        if frozenset(outputs) != _OUTPUT_NAMES:
            raise ValueError
        resource_id = output_value(outputs, "instance_id")
        endpoint = output_value(outputs, "base_url")
        cleanup = output_value(outputs, "cleanup_boundary")
        profile = output_value(outputs, "workload_profile_type")
        revision = output_value(outputs, "revision_name")
        if (
            output_value(outputs, "instance_ip") != resource_id
            or output_value(outputs, "endpoint_url") != endpoint
        ):
            raise ValueError
        evidence = AzureContainerAppDeploymentEvidence(
            resource_id=resource_id,
            cleanup_resource_id=cleanup,
            endpoint=endpoint,
            revision_name=revision,
            workload_profile_type=profile,
        )
        evidence.candidate_identity(policy)
        return evidence
    except Exception:
        raise AzureContainerAppMakeRuntimeError("output") from None


def validate_app_document(
    document: object,
    policy: AzureContainerAppLiveProofPolicy,
    evidence: AzureContainerAppDeploymentEvidence,
) -> None:
    """Match ARM application truth to Terraform evidence and immutable policy."""
    try:
        if document is None:
            raise ValueError
        app = mapping(document)
        resource_id = member(app, "id")
        name = member(app, "name")
        resource_type = member(app, "type")
        location = member(app, "location")
        if (
            not isinstance(resource_id, str)
            or resource_id.casefold() != policy.expected_resource_id.casefold()
            or name != policy.app_name
            or not isinstance(resource_type, str)
            or resource_type.casefold() != "microsoft.app/containerapps"
            or not isinstance(location, str)
            or location.replace(" ", "").casefold() != policy.location.casefold()
        ):
            raise ValueError
        properties = member(app, "properties")
        if (
            member(properties, "provisioningState") != "Succeeded"
            or member(properties, "latestReadyRevisionName") != evidence.revision_name
            or member(properties, "workloadProfileName")
            != policy.workload_profile_name
        ):
            raise ValueError
        configuration = member(properties, "configuration")
        ingress = member(configuration, "ingress")
        fqdn = member(ingress, "fqdn")
        if not isinstance(fqdn, str) or fqdn != urlsplit(evidence.endpoint).hostname:
            raise ValueError
        template = member(properties, "template")
        containers = member(template, "containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError
        container = containers[0]
        if member(container, "image") != policy.container_image:
            raise ValueError
        arguments = member(container, "args")
        required_argument(arguments, "--model", policy.model_name)
        required_argument(arguments, "--revision", policy.model_revision)
        required_argument(arguments, "--tokenizer-revision", policy.model_revision)
    except Exception:
        raise AzureContainerAppMakeRuntimeError("deployment-evidence") from None


__all__ = (
    "OWNERSHIP_MARKER",
    "deployment_outputs",
    "mapping",
    "member",
    "output_value",
    "read_bounded_json",
    "required_argument",
    "validate_app_document",
    "write_ownership_marker",
)
