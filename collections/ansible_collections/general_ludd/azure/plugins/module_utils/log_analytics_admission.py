"""Fail-closed Azure Log Analytics query admission on the controller."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

MAX_QUERY_BYTES = 32 * 1024
MAX_TABLES = 8
MAX_COLUMNS = 64
MAX_ROWS = 1_000
MAX_CELL_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_CELL_DEPTH = 16
MAX_CELL_ITEMS = 4_096
MIN_TIMESPAN_MINUTES = 1
MAX_TIMESPAN_MINUTES = 24 * 60
MAX_SERVER_TIMEOUT_SECONDS = 30

ENDPOINTS = {
    "public": "https://api.loganalytics.io",
    "government": "https://api.loganalytics.us",
    "china": "https://api.loganalytics.azure.cn",
}
TRUNCATION_PREFIX = (
    "set truncationmaxrecords=1001;\n"
    "set truncationmaxsize=4194304;\n"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_TRUNCATION_OVERRIDE_RE = re.compile(
    r"(?im)(?:^|;)\s*set\s+"
    r"(?:notruncation|truncationmaxrecords|truncationmaxsize)\b"
)


class LogAnalyticsAdmissionError(ValueError):
    """A bounded, secret-safe admission failure."""

    def as_result(self) -> dict[str, object]:
        """Translate the failure into a stable Ansible result."""
        return {
            "changed": False,
            "failed": True,
            "validated": False,
            "executed": False,
            "msg": str(self)[:512],
        }


class _Closable(Protocol):
    def close(self) -> None:
        """Release controller resources."""


class _LogsQueryClient(Protocol):
    def query_workspace(
        self,
        workspace_id: str,
        query: str,
        *,
        timespan: timedelta,
        server_timeout: int,
        include_statistics: bool,
        include_visualization: bool,
        additional_workspaces: None,
    ) -> object:
        """Execute one workspace-scoped query."""

    def close(self) -> None:
        """Release controller resources."""


@dataclass(frozen=True)
class CredentialConfig:
    """Exact credential selection without a developer-chain fallback."""

    mode: str
    managed_identity_client_id: str | None = None
    workload_tenant_id: str | None = None
    workload_client_id: str | None = None
    workload_token_file: str | None = None


CredentialFactory = Callable[[CredentialConfig], _Closable]
ClientFactory = Callable[[_Closable, str], _LogsQueryClient]
Progress = Callable[[str], None]


def _bounded_string(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or len(value) > maximum or "\x00" in value:
        raise LogAnalyticsAdmissionError(
            f"{field} must be a non-empty bounded string"
        )
    return value


def _canonical_uuid(value: object, field: str) -> str:
    selected = _bounded_string(value, field, maximum=36)
    try:
        parsed = uuid.UUID(selected)
    except ValueError as exc:
        raise LogAnalyticsAdmissionError(
            f"{field} must be a canonical UUID"
        ) from exc
    if str(parsed) != selected:
        raise LogAnalyticsAdmissionError(f"{field} must be a canonical UUID")
    return selected


def _optional_uuid(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _canonical_uuid(value, field)


def _bounded_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise LogAnalyticsAdmissionError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _credential_config(
    mode: object,
    *,
    managed_identity_client_id: object,
    workload_tenant_id: object,
    workload_client_id: object,
    workload_token_file: object,
) -> CredentialConfig:
    selected_mode = _bounded_string(mode, "credential_mode", maximum=16)
    if selected_mode not in {"managed", "workload", "environment"}:
        raise LogAnalyticsAdmissionError(
            "credential_mode must be managed, workload, or environment"
        )
    managed_client = _optional_uuid(
        managed_identity_client_id, "managed_identity_client_id"
    )
    tenant = _optional_uuid(workload_tenant_id, "workload_tenant_id")
    workload_client = _optional_uuid(
        workload_client_id, "workload_client_id"
    )
    token_file: str | None = None
    if workload_token_file not in (None, ""):
        token_file = _bounded_string(
            workload_token_file, "workload_token_file", maximum=1_024
        )
        if not Path(token_file).is_absolute():
            raise LogAnalyticsAdmissionError(
                "workload_token_file must be an absolute path"
            )

    if selected_mode == "managed":
        if tenant is not None or workload_client is not None or token_file is not None:
            raise LogAnalyticsAdmissionError(
                "managed credential mode rejects workload credential fields"
            )
    elif selected_mode == "workload":
        if managed_client is not None:
            raise LogAnalyticsAdmissionError(
                "workload credential mode rejects managed identity fields"
            )
        if tenant is None or workload_client is None or token_file is None:
            raise LogAnalyticsAdmissionError(
                "workload credential mode requires tenant, client, and token file"
            )
    elif any(
        value is not None
        for value in (managed_client, tenant, workload_client, token_file)
    ):
        raise LogAnalyticsAdmissionError(
            "environment credential mode rejects explicit identity fields"
        )

    return CredentialConfig(
        mode=selected_mode,
        managed_identity_client_id=managed_client,
        workload_tenant_id=tenant,
        workload_client_id=workload_client,
        workload_token_file=token_file,
    )


def _validated_query(query: object, digest: object) -> tuple[str, str]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not query or "\x00" in query:
        raise LogAnalyticsAdmissionError(
            "query must be a non-empty bounded string"
        )
    selected = query
    encoded = selected.encode("utf-8")
    if len(encoded) > MAX_QUERY_BYTES:
        raise LogAnalyticsAdmissionError("query exceeds the 32 KiB UTF-8 limit")
    if _FORBIDDEN_TRUNCATION_OVERRIDE_RE.search(selected):
        raise LogAnalyticsAdmissionError(
            "query must not override the enforced truncation policy"
        )
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise LogAnalyticsAdmissionError(
            "query_sha256 must be a lowercase SHA-256 digest"
        )
    observed = hashlib.sha256(encoded).hexdigest()
    if observed != digest:
        raise LogAnalyticsAdmissionError("query SHA-256 digest mismatch")
    return selected, digest


def _default_credential_factory(config: CredentialConfig) -> _Closable:
    from azure.identity import (
        EnvironmentCredential,
        ManagedIdentityCredential,
        WorkloadIdentityCredential,
    )

    if config.mode == "managed":
        if config.managed_identity_client_id is not None:
            return ManagedIdentityCredential(
                client_id=config.managed_identity_client_id
            )
        return ManagedIdentityCredential()
    if config.mode == "workload":
        if (
            config.workload_tenant_id is None
            or config.workload_client_id is None
            or config.workload_token_file is None
        ):
            raise LogAnalyticsAdmissionError(
                "workload credential configuration is incomplete"
            )
        return WorkloadIdentityCredential(
            tenant_id=config.workload_tenant_id,
            client_id=config.workload_client_id,
            token_file_path=config.workload_token_file,
        )
    return EnvironmentCredential()


def _default_client_factory(
    credential: _Closable,
    endpoint: str,
) -> _LogsQueryClient:
    from azure.core.credentials import TokenCredential
    from azure.monitor.query import LogsQueryClient

    return cast(
        _LogsQueryClient,
        LogsQueryClient(
            cast(TokenCredential, credential),
            endpoint=endpoint,
            retry_total=0,
            retry_connect=0,
            retry_read=0,
            retry_status=0,
        ),
    )


def _cell_value(value: object, *, depth: int = 0, budget: list[int] | None = None) -> object:
    if depth > MAX_CELL_DEPTH:
        raise LogAnalyticsAdmissionError("query cell exceeds the nesting limit")
    remaining = [MAX_CELL_ITEMS] if budget is None else budget
    remaining[0] -= 1
    if remaining[0] < 0:
        raise LogAnalyticsAdmissionError("query cell exceeds the item limit")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LogAnalyticsAdmissionError("query cell contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise LogAnalyticsAdmissionError("query cell contains a non-finite number")
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 1_024:
                raise LogAnalyticsAdmissionError(
                    "query cell object keys must be bounded strings"
                )
            if key in normalized:
                raise LogAnalyticsAdmissionError("query cell object keys must be unique")
            normalized[key] = _cell_value(
                item, depth=depth + 1, budget=remaining
            )
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _cell_value(item, depth=depth + 1, budget=remaining)
            for item in value
        ]
    raise LogAnalyticsAdmissionError("query cell contains an unsupported value")


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise LogAnalyticsAdmissionError(
            "query response is not canonical JSON"
        ) from exc


def _column(column: object) -> dict[str, str]:
    if isinstance(column, str):
        name = _bounded_string(column, "column name", maximum=512)
        return {"name": name}
    name = _bounded_string(getattr(column, "name", None), "column name", maximum=512)
    column_type = getattr(column, "type", None)
    if column_type is None:
        return {"name": name}
    return {
        "name": name,
        "type": _bounded_string(column_type, "column type", maximum=128),
    }


def _tables(response: object) -> tuple[list[dict[str, object]], int]:
    if response.__class__.__name__ == "LogsQueryPartialResult" or any(
        getattr(response, field, None) is not None
        for field in ("partial_data", "partial_error")
    ):
        raise LogAnalyticsAdmissionError(
            "partial Azure Log Analytics results are rejected"
        )
    status = getattr(response, "status", None)
    status_value = getattr(status, "value", status)
    if status is not None and status_value != "Success":
        raise LogAnalyticsAdmissionError(
            "partial or ambiguous Azure Log Analytics status is rejected"
        )
    if any(
        bool(getattr(response, field, False))
        for field in ("is_partial", "truncated", "continuation_token")
    ):
        raise LogAnalyticsAdmissionError(
            "partial or truncated Azure Log Analytics results are rejected"
        )
    raw_tables = getattr(response, "tables", None)
    if not isinstance(raw_tables, (list, tuple)):
        raise LogAnalyticsAdmissionError("query response has an ambiguous table shape")
    if len(raw_tables) > MAX_TABLES:
        raise LogAnalyticsAdmissionError("query response exceeds the 8-table limit")

    result: list[dict[str, object]] = []
    table_names: set[str] = set()
    total_rows = 0
    for table in raw_tables:
        name = _bounded_string(getattr(table, "name", None), "table name", maximum=512)
        if name in table_names:
            raise LogAnalyticsAdmissionError("query table names must be unique")
        table_names.add(name)
        raw_columns = getattr(table, "columns", None)
        raw_rows = getattr(table, "rows", None)
        if not isinstance(raw_columns, (list, tuple)) or not isinstance(
            raw_rows, (list, tuple)
        ):
            raise LogAnalyticsAdmissionError("query table has an ambiguous shape")
        if len(raw_columns) > MAX_COLUMNS:
            raise LogAnalyticsAdmissionError(
                "query response exceeds the 64-column limit"
            )
        columns = [_column(column) for column in raw_columns]
        raw_column_types = getattr(table, "columns_types", None)
        if raw_column_types is not None:
            if not isinstance(raw_column_types, (list, tuple)) or len(
                raw_column_types
            ) != len(columns):
                raise LogAnalyticsAdmissionError(
                    "query table has ambiguous column types"
                )
            columns = [
                {
                    **column,
                    "type": _bounded_string(
                        column_type,
                        "column type",
                        maximum=128,
                    ),
                }
                for column, column_type in zip(
                    columns, raw_column_types, strict=True
                )
            ]
        column_names = [column["name"] for column in columns]
        if len(column_names) != len(set(column_names)):
            raise LogAnalyticsAdmissionError("query column names must be unique")
        total_rows += len(raw_rows)
        if total_rows > MAX_ROWS:
            raise LogAnalyticsAdmissionError(
                "query response exceeds the 1000-row limit"
            )
        rows: list[list[object]] = []
        for raw_row in raw_rows:
            if isinstance(raw_row, (str, bytes, bytearray, Mapping)) or not (
                hasattr(raw_row, "__len__") and hasattr(raw_row, "__iter__")
            ):
                raise LogAnalyticsAdmissionError(
                    "query row width does not match the admitted columns"
                )
            try:
                row_values = list(raw_row)
            except (TypeError, ValueError) as exc:
                raise LogAnalyticsAdmissionError(
                    "query row width does not match the admitted columns"
                ) from exc
            if len(row_values) != len(columns):
                raise LogAnalyticsAdmissionError(
                    "query row width does not match the admitted columns"
                )
            row: list[object] = []
            for raw_cell in row_values:
                cell = _cell_value(raw_cell)
                if len(_canonical_bytes(cell)) > MAX_CELL_BYTES:
                    raise LogAnalyticsAdmissionError(
                        "query response exceeds the 16 KiB cell limit"
                    )
                row.append(cell)
            rows.append(row)
        result.append({"name": name, "columns": columns, "rows": rows})
    return result, total_rows


def _close(resource: object | None) -> bool:
    if resource is None:
        return True
    close = getattr(resource, "close", None)
    if not callable(close):
        return False
    try:
        close()
    except Exception:
        return False
    return True


def execute_log_analytics_query(
    *,
    workspace_id: object,
    query: object,
    query_sha256: object,
    timespan_minutes: object = 5,
    endpoint: object = "public",
    credential_mode: object = "managed",
    server_timeout_seconds: object = 15,
    managed_identity_client_id: object = None,
    workload_tenant_id: object = None,
    workload_client_id: object = None,
    workload_token_file: object = None,
    validate_only: object = False,
    credential_factory: CredentialFactory | None = None,
    client_factory: ClientFactory | None = None,
    progress: Progress | None = None,
) -> dict[str, object]:
    """Validate and execute one exact bounded LogsQueryClient request."""
    selected_workspace = _canonical_uuid(workspace_id, "workspace_id")
    selected_query, selected_digest = _validated_query(query, query_sha256)
    selected_timespan = _bounded_integer(
        timespan_minutes,
        "timespan_minutes",
        minimum=MIN_TIMESPAN_MINUTES,
        maximum=MAX_TIMESPAN_MINUTES,
    )
    selected_endpoint = _bounded_string(endpoint, "endpoint", maximum=16)
    if selected_endpoint not in ENDPOINTS:
        raise LogAnalyticsAdmissionError(
            "endpoint must be public, government, or china"
        )
    selected_timeout = _bounded_integer(
        server_timeout_seconds,
        "server_timeout_seconds",
        minimum=1,
        maximum=MAX_SERVER_TIMEOUT_SECONDS,
    )
    credentials = _credential_config(
        credential_mode,
        managed_identity_client_id=managed_identity_client_id,
        workload_tenant_id=workload_tenant_id,
        workload_client_id=workload_client_id,
        workload_token_file=workload_token_file,
    )
    if not isinstance(validate_only, bool):
        raise TypeError("validate_only must be a boolean")

    common: dict[str, object] = {
        "changed": False,
        "validated": True,
        "executed": False,
        "workspace_id": selected_workspace,
        "query_sha256": selected_digest,
        "endpoint": selected_endpoint,
        "timespan_minutes": selected_timespan,
        "server_timeout_seconds": selected_timeout,
        "tables": [],
        "row_count": 0,
    }
    if validate_only:
        return common

    emit = progress if progress is not None else lambda _message: None
    make_credential = (
        credential_factory
        if credential_factory is not None
        else _default_credential_factory
    )
    make_client = client_factory if client_factory is not None else _default_client_factory
    credential: object | None = None
    client: object | None = None
    failure: BaseException | None = None
    try:
        emit("azure_log_analytics: create exact credential")
        credential = make_credential(credentials)
        emit("azure_log_analytics: create no-retry LogsQueryClient")
        client = make_client(credential, ENDPOINTS[selected_endpoint])
        query_workspace = getattr(client, "query_workspace", None)
        if not callable(query_workspace):
            raise LogAnalyticsAdmissionError(
                "LogsQueryClient has no query_workspace operation"
            )
        emit("azure_log_analytics: execute bounded workspace query")
        response = query_workspace(
            selected_workspace,
            TRUNCATION_PREFIX + selected_query,
            timespan=timedelta(minutes=selected_timespan),
            server_timeout=selected_timeout,
            include_statistics=False,
            include_visualization=False,
            additional_workspaces=None,
        )
        tables, row_count = _tables(response)
        result = {
            **common,
            "executed": True,
            "tables": tables,
            "row_count": row_count,
        }
        if len(_canonical_bytes(result)) > MAX_OUTPUT_BYTES:
            raise LogAnalyticsAdmissionError(
                "query response exceeds the 2 MiB canonical output limit"
            )
        emit("azure_log_analytics: bounded workspace query complete")
        return result
    except (LogAnalyticsAdmissionError, TypeError, ValueError) as exc:
        failure = exc
        raise
    except Exception as exc:
        failure = exc
        raise LogAnalyticsAdmissionError(
            "Azure Log Analytics query failed"
        ) from exc
    finally:
        client_cleanup_ok = _close(client)
        credential_cleanup_ok = _close(credential)
        cleanup_ok = client_cleanup_ok and credential_cleanup_ok
        if not cleanup_ok and failure is None and sys.exc_info()[0] is None:
            raise LogAnalyticsAdmissionError(
                "Azure Log Analytics client cleanup failed"
            )


def validation_plan(workspace_id: object, query: object) -> dict[str, object]:
    """Return a compatibility plan that cannot be mistaken for execution."""
    selected_workspace = _canonical_uuid(workspace_id, "workspace_id")
    if not isinstance(query, str) or not query:
        raise LogAnalyticsAdmissionError("query must be a non-empty bounded string")
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    _validated_query(query, digest)
    return {
        "status": "validated",
        "result": {
            "workspace_id": selected_workspace,
            "query_sha256": digest,
            "timespan": "PT5M",
            "executed": False,
        },
        "warnings": [
            "validation only; execute with general_ludd.azure.log_analytics_query"
        ],
    }


_ACTION_FIELDS = frozenset(
    {
        "workspace_id",
        "query",
        "query_sha256",
        "timespan_minutes",
        "endpoint",
        "credential_mode",
        "server_timeout_seconds",
        "managed_identity_client_id",
        "workload_tenant_id",
        "workload_client_id",
        "workload_token_file",
        "validate_only",
    }
)


def action_arguments(args: Mapping[str, object]) -> dict[str, object]:
    """Return a strict copy of supported Ansible action arguments."""
    unknown = sorted(set(args) - _ACTION_FIELDS)
    if unknown:
        raise LogAnalyticsAdmissionError(
            f"unsupported log analytics arguments: {', '.join(unknown)}"
        )
    missing = sorted(
        field
        for field in ("workspace_id", "query", "query_sha256")
        if field not in args
    )
    if missing:
        raise TypeError(f"missing required argument: {', '.join(missing)}")
    return dict(args)


__all__ = [
    "ENDPOINTS",
    "TRUNCATION_PREFIX",
    "ClientFactory",
    "CredentialConfig",
    "CredentialFactory",
    "LogAnalyticsAdmissionError",
    "action_arguments",
    "execute_log_analytics_query",
    "validation_plan",
]
