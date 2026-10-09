"""Digest-bound OpenAPI 3.1 registration and bounded read-only execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode, urlsplit

import yaml
from ansible_collections.general_ludd.infrastructure.plugins.module_utils.secure_fetch import (
    FetchPolicy,
    FetchResult,
    SecureFetchError,
    secure_fetch,
)
from jsonpointer import JsonPointerException, resolve_pointer
from jsonschema_path import SchemaPath
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters

OPENAPI_CORE_VERSION = "0.23.1"
JSONPOINTER_VERSION = "3.2.0"
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_SPEC_NODES = 50_000
MAX_SPEC_DEPTH = 64
MAX_OPERATIONS = 512
MAX_RESPONSES = 64
MAX_PARAMETERS = 32
MAX_PARAMETER_BYTES = 4096
MAX_SECRET_BYTES = 4096
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 1000
MAX_TIMEOUT_SECONDS = 30.0
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_CONTROLLED_HEADERS = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_METHODS = frozenset({"get", "head"})
_PATH_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_ALLOWED_ARGS = frozenset(
    {
        "root",
        "spec_path",
        "spec_sha256",
        "base_url",
        "allowed_host",
        "operation_id",
        "parameters",
        "secret_env",
        "records_pointer",
        "timeout_seconds",
        "max_response_bytes",
        "max_records",
        "validate_only",
    }
)


class OpenAPIRegistrationError(ValueError):
    """A stable fail-closed rejection safe to return through Ansible."""

    def as_result(self) -> dict[str, object]:
        """Return a bounded failure result without request secrets or response data."""
        return {
            "changed": False,
            "executed": False,
            "failed": True,
            "msg": " ".join(str(self).split())[:512],
            "validated": False,
        }


Fetcher = Callable[..., FetchResult]
Progress = Callable[[str], None]


def _ignore_progress(_phase: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Downward-only query limits used by tests and the public adapter."""

    timeout_seconds: float = 15.0
    max_response_bytes: int = 1024 * 1024
    max_records: int = 100

    def validate(self) -> None:
        """Reject non-positive limits and attempts to exceed hard ceilings."""
        if not (0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise OpenAPIRegistrationError("timeout must be above zero and at most 30 seconds")
        if not (1 <= self.max_response_bytes <= MAX_RESPONSE_BYTES):
            raise OpenAPIRegistrationError("response limit must be between 1 byte and 4 MiB")
        if not (1 <= self.max_records <= MAX_RECORDS):
            raise OpenAPIRegistrationError("record limit must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class _Operation:
    path: str
    method: str
    parameters: tuple[Mapping[str, object], ...]


@dataclass(slots=True)
class _OpenAPIRequest:
    host_url: str
    path: str
    path_pattern: str
    method: str
    parameters: RequestParameters
    body: bytes | None = None
    content_type: str = ""

    @property
    def full_url_pattern(self) -> str:
        return f"{self.host_url}{self.path_pattern}"


@dataclass(frozen=True, slots=True)
class _OpenAPIResponse:
    status_code: int
    headers: Mapping[str, object]
    data: bytes | None
    content_type: str


def _root(value: object) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value.strip():
        raise OpenAPIRegistrationError("root must be a non-empty absolute local path")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise OpenAPIRegistrationError("root must be an absolute local path")
    try:
        metadata = os.lstat(supplied)
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise OpenAPIRegistrationError("root is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OpenAPIRegistrationError("root must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode) or not resolved.is_dir():
        raise OpenAPIRegistrationError("root must be a directory")
    return supplied, resolved


def _regular_spec(root_input: Path, root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise OpenAPIRegistrationError("spec_path must be a non-empty local path")
    supplied = Path(value)
    if ".." in supplied.parts:
        raise OpenAPIRegistrationError("spec_path escapes root")
    candidate = supplied if supplied.is_absolute() else root_input / supplied
    current = root_input
    try:
        relative_input = candidate.relative_to(root_input)
    except ValueError:
        relative_input = Path("..")
    for part in relative_input.parts:
        current /= part
        if current.is_symlink():
            raise OpenAPIRegistrationError("spec_path must not contain a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        metadata = resolved.stat()
    except (OSError, ValueError) as exc:
        raise OpenAPIRegistrationError("spec_path escapes root or is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OpenAPIRegistrationError("spec_path must be one regular, unlinked file")
    if metadata.st_size < 1 or metadata.st_size > MAX_SPEC_BYTES:
        raise OpenAPIRegistrationError("OpenAPI input must be between 1 byte and 2 MiB")
    return resolved


def _read_stable(path: Path, expected_digest: object) -> tuple[bytes, str]:
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise OpenAPIRegistrationError("spec_sha256 must be a lowercase SHA-256 digest")
    if not hasattr(os, "O_NOFOLLOW"):
        raise OpenAPIRegistrationError(
            "controller does not support no-follow contract reads"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise OpenAPIRegistrationError("OpenAPI input became unavailable") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            before = os.fstat(descriptor)
            content = handle.read(MAX_SPEC_BYTES + 1)
            after = os.fstat(descriptor)
        current = os.lstat(path)
    except OSError as exc:
        raise OpenAPIRegistrationError("OpenAPI input became unavailable") from exc
    finally:
        os.close(descriptor)
    before_id = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_id = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    current_id = (current.st_dev, current.st_ino)
    if (
        before_id != after_id
        or current_id != (after.st_dev, after.st_ino)
        or not stat.S_ISREG(after.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or after.st_nlink != 1
        or len(content) != after.st_size
        or len(content) > MAX_SPEC_BYTES
    ):
        raise OpenAPIRegistrationError("OpenAPI input changed during admission")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_digest:
        raise OpenAPIRegistrationError("OpenAPI input digest does not match spec_sha256")
    return content, actual


def _bounded_tree(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 0)]
    seen = 0
    while pending:
        item, depth = pending.pop()
        seen += 1
        if seen > MAX_SPEC_NODES:
            raise OpenAPIRegistrationError("OpenAPI input exceeds the node limit")
        if depth > MAX_SPEC_DEPTH:
            raise OpenAPIRegistrationError("OpenAPI input exceeds the nesting limit")
        if isinstance(item, Mapping):
            reference = item.get("$ref")
            if reference is not None and (
                not isinstance(reference, str) or not reference.startswith("#/")
            ):
                raise OpenAPIRegistrationError(
                    "OpenAPI references may use only an internal JSON Pointer"
                )
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _validate_internal_references(spec: Mapping[str, object]) -> None:
    pending: list[object] = [spec]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            reference = item.get("$ref")
            if isinstance(reference, str):
                try:
                    resolve_pointer(spec, reference[1:])
                except JsonPointerException as exc:
                    raise OpenAPIRegistrationError(
                        "OpenAPI reference cannot be resolved"
                    ) from exc
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _load_spec(
    content: bytes,
    progress: Progress | None = None,
) -> tuple[dict[str, object], OpenAPI]:
    announce = progress or _ignore_progress
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise OpenAPIRegistrationError("OpenAPI input is not valid JSON or YAML") from exc
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise OpenAPIRegistrationError("OpenAPI input must be an object with string keys")
    spec = cast(dict[str, object], loaded)
    announce("openapi_query: OpenAPI document parsed")
    _bounded_tree(spec)
    announce("openapi_query: OpenAPI structure bounded")
    _validate_internal_references(spec)
    announce("openapi_query: OpenAPI references confined")
    version = spec.get("openapi")
    if not isinstance(version, str) or not version.startswith("3.1."):
        raise OpenAPIRegistrationError("OpenAPI input must declare OpenAPI 3.1")
    info = spec.get("info")
    paths = spec.get("paths")
    if (
        not isinstance(info, Mapping)
        or not isinstance(info.get("title"), str)
        or not info.get("title")
        or not isinstance(info.get("version"), str)
        or not info.get("version")
        or not isinstance(paths, Mapping)
    ):
        raise OpenAPIRegistrationError("OpenAPI 3.1 specification validation failed")
    try:
        schema_path = SchemaPath.from_dict(spec, handlers={})
        announce("openapi_query: local-only schema path created")
        validator = OpenAPI(schema_path, config=Config(spec_validator_cls=None))
    except Exception as exc:
        raise OpenAPIRegistrationError("OpenAPI 3.1 specification validation failed") from exc
    return spec, validator


def _resolve(document: Mapping[str, object], value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OpenAPIRegistrationError(f"{label} must be an object")
    reference = value.get("$ref")
    if reference is None:
        return cast(Mapping[str, object], value)
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise OpenAPIRegistrationError(f"{label} may use only an internal JSON Pointer")
    try:
        resolved = resolve_pointer(document, reference[1:])
    except JsonPointerException as exc:
        raise OpenAPIRegistrationError(f"{label} reference cannot be resolved") from exc
    if not isinstance(resolved, Mapping):
        raise OpenAPIRegistrationError(f"{label} reference must resolve to an object")
    return cast(Mapping[str, object], resolved)


def _parameter_list(document: Mapping[str, object], raw: object, label: str) -> list[Mapping[str, object]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OpenAPIRegistrationError(f"{label} parameters must be a list")
    return [_resolve(document, item, f"{label} parameter") for item in raw]


def _validate_json_response_contract(
    document: Mapping[str, object],
    operation: Mapping[str, object],
) -> None:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping) or not responses or len(responses) > MAX_RESPONSES:
        raise OpenAPIRegistrationError(
            "selected operation must define a bounded response contract"
        )
    found_json_schema = False
    for raw_response in responses.values():
        response = _resolve(document, raw_response, "response")
        if not isinstance(response.get("description"), str):
            raise OpenAPIRegistrationError(
                "selected operation response contract requires a description"
            )
        content = response.get("content")
        if content is None:
            continue
        if not isinstance(content, Mapping):
            raise OpenAPIRegistrationError("response content must be an object")
        for media_type, raw_media in content.items():
            if not isinstance(media_type, str) or len(media_type) > 128:
                raise OpenAPIRegistrationError("response media type must be bounded")
            media = _resolve(document, raw_media, "response media type")
            if media_type.lower() == "application/json" or media_type.lower().endswith("+json"):
                _resolve(document, media.get("schema"), "response schema")
                found_json_schema = True
    if not found_json_schema:
        raise OpenAPIRegistrationError(
            "selected operation must define a JSON response schema"
        )


def _find_operation(spec: Mapping[str, object], operation_id: object) -> _Operation:
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
        raise OpenAPIRegistrationError("operation_id must be a non-empty string of at most 128 characters")
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise OpenAPIRegistrationError("OpenAPI input must define paths")
    matches: list[_Operation] = []
    count = 0
    for path, raw_path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 2048:
            raise OpenAPIRegistrationError("OpenAPI paths must be bounded absolute paths")
        path_item = _resolve(spec, raw_path_item, "path item")
        inherited = _parameter_list(spec, path_item.get("parameters"), "path")
        for method, raw_operation in path_item.items():
            if method not in _PATH_METHODS:
                continue
            count += 1
            if count > MAX_OPERATIONS:
                raise OpenAPIRegistrationError("OpenAPI input exceeds the operation limit")
            operation = _resolve(spec, raw_operation, "operation")
            if operation.get("operationId") == operation_id:
                _validate_json_response_contract(spec, operation)
                parameters = inherited + _parameter_list(spec, operation.get("parameters"), "operation")
                matches.append(_Operation(path, method.upper(), tuple(parameters)))
    if len(matches) != 1:
        raise OpenAPIRegistrationError("operation_id must identify exactly one operation")
    selected = matches[0]
    if selected.method.lower() not in _METHODS:
        raise OpenAPIRegistrationError("registered operations must use GET or HEAD")
    return selected


def _base_url(base_url: object, allowed_host: object) -> tuple[str, str]:
    if not isinstance(base_url, str) or not isinstance(allowed_host, str):
        raise TypeError("base_url and allowed_host must be strings")
    try:
        parsed = urlsplit(base_url)
    except ValueError as exc:
        raise OpenAPIRegistrationError("base_url must be a valid HTTPS URL") from exc
    normalized_host = allowed_host.strip().lower().rstrip(".")
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        raise OpenAPIRegistrationError("base_url must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OpenAPIRegistrationError("base_url must not contain credentials, query, or fragment")
    if not host or host != normalized_host:
        raise OpenAPIRegistrationError("base_url host must exactly match allowed_host")
    if "/" in normalized_host or "*" in normalized_host or len(normalized_host) > 253:
        raise OpenAPIRegistrationError("allowed_host must be one explicit DNS host")
    prefix = f"https://{parsed.netloc}{parsed.path.rstrip('/')}"
    return prefix, normalized_host


def _scalar(value: object, name: str) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise OpenAPIRegistrationError(f"parameter {name!r} must be finite")
        text = str(value)
    elif isinstance(value, (str, int)):
        text = str(value)
    else:
        raise TypeError(f"parameter {name!r} must be a scalar")
    if not text or len(text.encode()) > 512 or "\x00" in text:
        raise OpenAPIRegistrationError(f"parameter {name!r} must be between 1 and 512 bytes")
    return text


def _request_parts(
    operation: _Operation,
    supplied: object,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]]:
    if not isinstance(supplied, Mapping) or not all(isinstance(key, str) for key in supplied):
        raise TypeError("parameters must be a mapping with string keys")
    if len(supplied) > MAX_PARAMETERS:
        raise OpenAPIRegistrationError("parameters exceed the 32-parameter limit")
    definitions: dict[str, tuple[str, bool]] = {}
    defined_headers: set[str] = set()
    for item in operation.parameters:
        name = item.get("name")
        location = item.get("in")
        if not isinstance(name, str) or not isinstance(location, str):
            raise OpenAPIRegistrationError("operation parameter definitions must include name and location")
        if location not in {"path", "query", "header"}:
            raise OpenAPIRegistrationError("only path, query, and header parameters are supported")
        if name in definitions:
            raise OpenAPIRegistrationError("operation parameter names must be unique")
        if location == "header":
            normalized = name.lower()
            if normalized in _CONTROLLED_HEADERS:
                raise OpenAPIRegistrationError(
                    "operation header parameter collides with a controlled header"
                )
            if normalized in defined_headers:
                raise OpenAPIRegistrationError(
                    "operation header names must be case-insensitively unique"
                )
            defined_headers.add(normalized)
        definitions[name] = (location, bool(item.get("required", False)))
    unknown = sorted(set(supplied) - set(definitions))
    if unknown:
        raise OpenAPIRegistrationError(f"unknown parameter: {unknown[0]}")
    missing = sorted(name for name, (_location, required) in definitions.items() if required and name not in supplied)
    if missing:
        raise OpenAPIRegistrationError(f"required parameter is missing: {missing[0]}")
    path_values: dict[str, str] = {}
    query_values: dict[str, str] = {}
    header_values: dict[str, str] = {}
    total = 0
    for name, value in supplied.items():
        text = _scalar(value, name)
        total += len(name.encode()) + len(text.encode())
        location = definitions[name][0]
        target = {"path": path_values, "query": query_values, "header": header_values}[location]
        target[name] = text
    if total > MAX_PARAMETER_BYTES:
        raise OpenAPIRegistrationError("parameters exceed the 4096-byte limit")
    concrete_path = operation.path
    for name, value in path_values.items():
        concrete_path = concrete_path.replace("{" + name + "}", quote(value, safe=""))
    if "{" in concrete_path or "}" in concrete_path:
        raise OpenAPIRegistrationError("all OpenAPI path parameters must be supplied")
    return concrete_path, path_values, query_values, header_values


def _secret_headers(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise TypeError("secret_env must map header names to environment variable names")
    if len(value) > 16:
        raise OpenAPIRegistrationError("secret_env exceeds the 16-header limit")
    headers: dict[str, str] = {}
    normalized_headers: set[str] = set()
    for header, env_name in value.items():
        if _HEADER_NAME.fullmatch(header) is None:
            raise OpenAPIRegistrationError("secret_env contains an invalid header name")
        normalized = header.lower()
        if normalized in _CONTROLLED_HEADERS:
            label = "Accept" if normalized == "accept" else header
            raise OpenAPIRegistrationError(
                f"secret header {label} is reserved or controlled"
            )
        if normalized in normalized_headers:
            raise OpenAPIRegistrationError(
                "secret header names must be case-insensitively unique"
            )
        normalized_headers.add(normalized)
        if _ENV_NAME.fullmatch(env_name) is None:
            raise OpenAPIRegistrationError("secret_env contains an invalid environment variable name")
        secret = os.environ.get(env_name)
        if secret is None:
            raise OpenAPIRegistrationError(f"secret environment variable {env_name} is not set")
        if not secret or len(secret.encode()) > MAX_SECRET_BYTES or "\r" in secret or "\n" in secret:
            raise OpenAPIRegistrationError(f"secret environment variable {env_name} is invalid")
        headers[header] = secret
    return headers


def _decode_records(response: FetchResult, pointer: object, max_records: int) -> list[object]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not response.content:
        return []
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise OpenAPIRegistrationError("response content type is not JSON")
    try:
        payload = json.loads(response.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAPIRegistrationError("response body is not valid JSON") from exc
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")) or len(pointer) > 512:
        raise OpenAPIRegistrationError("records_pointer must be empty or a bounded JSON Pointer")
    try:
        selected = resolve_pointer(payload, pointer) if pointer else payload
    except JsonPointerException as exc:
        raise OpenAPIRegistrationError("records_pointer cannot be resolved") from exc
    if not isinstance(selected, list):
        raise OpenAPIRegistrationError("records_pointer must resolve to a list")
    if len(selected) > max_records:
        raise OpenAPIRegistrationError("response exceeds the record limit")
    return cast(list[object], selected)


def action_arguments(args: Mapping[str, object]) -> dict[str, object]:
    """Validate the public key surface and normalize defaults."""
    unsupported = sorted(set(args) - _ALLOWED_ARGS)
    if unsupported:
        raise TypeError(f"unsupported argument: {unsupported[0]}")
    required = ("root", "spec_path", "spec_sha256", "base_url", "allowed_host", "operation_id")
    missing = [name for name in required if name not in args]
    if missing:
        raise TypeError(f"missing required argument: {missing[0]}")
    return {
        **args,
        "parameters": args.get("parameters", {}),
        "secret_env": args.get("secret_env", {}),
        "records_pointer": args.get("records_pointer", ""),
        "timeout_seconds": args.get("timeout_seconds", 15.0),
        "max_response_bytes": args.get("max_response_bytes", 1024 * 1024),
        "max_records": args.get("max_records", 100),
        "validate_only": args.get("validate_only", False),
    }


def execute_openapi_query(
    *,
    root: object,
    spec_path: object,
    spec_sha256: object,
    base_url: object,
    allowed_host: object,
    operation_id: object,
    parameters: object = None,
    secret_env: object = None,
    records_pointer: object = "",
    timeout_seconds: object = 15.0,
    max_response_bytes: object = 1024 * 1024,
    max_records: object = 100,
    validate_only: object = False,
    fetcher: Fetcher = secure_fetch,
    progress: Progress | None = None,
) -> dict[str, object]:
    """Validate one local contract and optionally execute its read-only operation."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError("timeout_seconds must be a number")
    if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
        raise TypeError("max_response_bytes must be an integer")
    if isinstance(max_records, bool) or not isinstance(max_records, int):
        raise TypeError("max_records must be an integer")
    if not isinstance(validate_only, bool):
        raise TypeError("validate_only must be a boolean")
    announce = progress or _ignore_progress
    limits = QueryLimits(float(timeout_seconds), max_response_bytes, max_records)
    limits.validate()
    announce("openapi_query: limits accepted")
    root_input, resolved_root = _root(root)
    spec_file = _regular_spec(root_input, resolved_root, spec_path)
    content, actual_digest = _read_stable(spec_file, spec_sha256)
    announce("openapi_query: local digest verified")
    spec, validator = _load_spec(content, announce)
    announce("openapi_query: OpenAPI 3.1 contract loaded")
    operation = _find_operation(spec, operation_id)
    host_url, host = _base_url(base_url, allowed_host)
    announce("openapi_query: read-only operation admitted")
    concrete_path, path_parameters, query_parameters, header_parameters = _request_parts(
        operation,
        parameters if parameters is not None else {},
    )
    secret_headers = _secret_headers(secret_env if secret_env is not None else {})
    if {name.lower() for name in header_parameters} & {
        name.lower() for name in secret_headers
    }:
        raise OpenAPIRegistrationError(
            "secret header collides with an operation header parameter"
        )
    headers = {
        "Accept": "application/json",
        **header_parameters,
        **secret_headers,
    }
    request = _OpenAPIRequest(
        host_url=host_url,
        path=concrete_path,
        path_pattern=operation.path,
        method=operation.method.lower(),
        parameters=RequestParameters(
            query=query_parameters,
            header=headers,
            path=path_parameters,
        ),
    )
    try:
        validator.validate_request(request)
    except Exception as exc:
        raise OpenAPIRegistrationError("OpenAPI request validation failed") from exc
    announce("openapi_query: request schema validated")
    base_result: dict[str, object] = {
        "changed": False,
        "executed": False,
        "method": operation.method,
        "operation_id": operation_id,
        "path": operation.path,
        "spec_sha256": actual_digest,
        "validated": True,
    }
    if validate_only:
        return base_result
    query = urlencode(query_parameters)
    request_url = f"{host_url}{concrete_path}{'?' + query if query else ''}"
    policy = FetchPolicy(
        allowed_hosts=frozenset({host}),
        allowed_schemes=frozenset({"https"}),
        max_bytes=limits.max_response_bytes,
        timeout_seconds=limits.timeout_seconds,
        dns_timeout_seconds=min(2.0, limits.timeout_seconds),
        max_redirects=0,
    )
    try:
        response = fetcher(
            request_url,
            policy=policy,
            method=operation.method,
            headers=headers,
        )
    except SecureFetchError as exc:
        raise OpenAPIRegistrationError("bounded HTTPS query failed") from exc
    if not isinstance(response, FetchResult):
        raise OpenAPIRegistrationError("bounded HTTPS transport returned an invalid result")
    content_type = response.headers.get("content-type", "").lower()
    protocol_response = _OpenAPIResponse(
        status_code=response.status_code,
        headers=response.headers,
        data=response.content,
        content_type=content_type,
    )
    try:
        validator.validate_response(request, protocol_response)
    except Exception as exc:
        raise OpenAPIRegistrationError("OpenAPI response validation failed") from exc
    records = _decode_records(response, records_pointer, limits.max_records)
    return {
        **base_result,
        "executed": True,
        "record_count": len(records),
        "records": records,
        "status_code": response.status_code,
    }


__all__ = [
    "JSONPOINTER_VERSION",
    "OPENAPI_CORE_VERSION",
    "OpenAPIRegistrationError",
    "QueryLimits",
    "action_arguments",
    "execute_openapi_query",
]
