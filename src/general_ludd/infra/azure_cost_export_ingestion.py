"""Durable Azure ActualCost Export ingestion and column-mapped parsing.

Azure Cost Management Exports produce CSV/Parquet blobs in a storage account.
This module parses CSV exports faithfully, preserving every source column and
computing a deterministic row identity so re-ingestion is idempotent across
worker restarts.

Schema pinning (API version and column names per version) rejects drift at
parse time rather than silently mapping unknown columns.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from general_ludd.infra.azure_cost_reconciliation import (
    AzureActualCostObservation,
)


class AzureCostExportError(RuntimeError):
    """Base for all export-level ingestion failures."""


class AzureCostExportParseError(AzureCostExportError):
    """Raised when an export blob cannot be parsed into cost observations."""


class AzureCostExportCompletenessError(AzureCostExportError):
    """Raised when an export snapshot fails a completeness gate."""


# ---------------------------------------------------------------------------
# Column-map contract (schema pinning)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureCostExportColumnMap:
    """Pinned column names for one export API version.

    If Azure renames or removes a column in a newer version, the map changes
    and the old version's columns are no longer accepted.
    """

    resource_id: str
    meter_id: str
    cost_in_billing_currency: str
    billing_currency_code: str
    charge_type: str
    service_name: str
    date: str

    @classmethod
    def default(cls) -> AzureCostExportColumnMap:
        """Column names from the 2025-03-01 ActualCost Export schema."""
        return cls(
            resource_id="ResourceId",
            meter_id="MeterId",
            cost_in_billing_currency="CostInBillingCurrency",
            billing_currency_code="BillingCurrencyCode",
            charge_type="ChargeType",
            service_name="ServiceName",
            date="Date",
        )

    def required_column_names(self) -> frozenset[str]:
        return frozenset(
            {
                self.resource_id,
                self.meter_id,
                self.cost_in_billing_currency,
                self.billing_currency_code,
                self.charge_type,
                self.service_name,
                self.date,
            }
        )

    def expected_column_names(self) -> frozenset[str]:
        return frozenset(
            {
                "BillingAccountId",
                self.date,
                self.resource_id,
                self.meter_id,
                "MeterCategory",
                "MeterSubCategory",
                "MeterName",
                "MeterRegion",
                "UnitOfMeasure",
                "Quantity",
                "EffectivePrice",
                self.cost_in_billing_currency,
                "CostInPricingCurrency",
                "PricingCurrencyCode",
                self.billing_currency_code,
                self.service_name,
                "ServiceTier",
                self.charge_type,
                "BillingPeriodStartDate",
                "BillingPeriodEndDate",
                "ResourceGroup",
                "ResourceLocation",
                "ConsumedService",
                "InvoiceSection",
                "SubscriptionId",
                "SubscriptionName",
                "ProductName",
                "PublisherType",
                "AdditionalInfo",
                "Tags",
                "CostAllocationRuleName",
            }
        )


# ---------------------------------------------------------------------------
# Schema version registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureCostExportSchema:
    version: int
    column_map: AzureCostExportColumnMap

    _REGISTRY: dict[int, AzureCostExportSchema] = field(default_factory=dict, init=False, compare=False)

    def __class_getitem__(cls, item: int) -> AzureCostExportSchema:
        raise TypeError("use AzureCostExportSchema.get(version=N)")

    @classmethod
    def get(cls, version: int) -> AzureCostExportSchema:
        registry = {1: ACTUALCOST_EXPORT_SCHEMA_V1}
        if version not in registry:
            raise ValueError(f"unsupported export schema version {version}. Known versions: {sorted(registry)}")
        return registry[version]


ACTUALCOST_EXPORT_SCHEMA_V1 = AzureCostExportSchema(
    version=1,
    column_map=AzureCostExportColumnMap.default(),
)


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AzureCostExportParseError(f"{name} must be a non-empty string")


def _compute_row_identity(
    source: str,
    snapshot_id: str,
    row_index: int,
    resource_id: str,
    meter_id: str,
    date: str,
) -> str:
    material = f"{source}|{snapshot_id}|{row_index}|{resource_id}|{meter_id}|{date}"
    return f"{source}:{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def _parse_csv_rows_to_dicts(
    content: str,
    column_map: AzureCostExportColumnMap,
    *,
    strict_columns: bool,
) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise AzureCostExportParseError("CSV has no header row")

    field_names = set(reader.fieldnames)
    _require_text("header content", ",".join(reader.fieldnames))

    required = column_map.required_column_names()
    missing = required - field_names
    if missing:
        raise AzureCostExportParseError(f"CSV is missing required columns: {sorted(missing)}")

    if strict_columns:
        unknown = field_names - column_map.expected_column_names()
        if unknown:
            raise AzureCostExportParseError(
                f"Strict mode: CSV contains unknown columns {sorted(unknown)}. "
                f"Set strict_columns=False to allow extra columns, or update the schema map."
            )

    rows: list[dict[str, str]] = list(reader)
    if not rows:
        raise AzureCostExportParseError("CSV has header but no data rows (empty export)")

    return rows


def parse_actual_cost_csv(
    content: str,
    source: str,
    snapshot_id: str,
    now: datetime,
    *,
    column_map: AzureCostExportColumnMap | None = None,
    strict_columns: bool = True,
    reject_nonpositive_cost: bool = True,
) -> list[AzureActualCostObservation]:
    """Parse an ActualCost Export CSV blob into immutable observations.

    Every source column is preserved in the observation payload so downstream
    users can compute totals, allocations, or forensic evidence without
    re-reading the original blob.
    """
    _require_text("source", source)
    _require_text("snapshot_id", snapshot_id)
    effective_map = column_map or AzureCostExportColumnMap.default()

    rows = _parse_csv_rows_to_dicts(
        content,
        effective_map,
        strict_columns=strict_columns,
    )

    observations: list[AzureActualCostObservation] = []
    for index, row in enumerate(rows):
        resource_id = row.get(effective_map.resource_id, "").strip()
        meter_id = row.get(effective_map.meter_id, "").strip()
        raw_cost = row.get(effective_map.cost_in_billing_currency, "0").strip()
        currency = row.get(effective_map.billing_currency_code, "").strip().upper()
        date = row.get(effective_map.date, "").strip()

        if not resource_id:
            raise AzureCostExportParseError(f"Row {index}: resource_id is empty")
        if not currency:
            raise AzureCostExportParseError(f"Row {index}: billing_currency_code is empty")
        if currency != "USD":
            raise AzureCostExportParseError(f"Row {index}: currency must be USD, got {currency!r}")

        try:
            cost_usd = float(raw_cost)
        except (ValueError, TypeError) as exc:
            raise AzureCostExportParseError(f"Row {index}: cannot parse cost as float: {raw_cost!r}") from exc

        if reject_nonpositive_cost and cost_usd <= 0:
            raise AzureCostExportParseError(f"Row {index}: cost must be positive, got {cost_usd}")

        row_identity = _compute_row_identity(
            source=source,
            snapshot_id=snapshot_id,
            row_index=index,
            resource_id=resource_id.lower(),
            meter_id=meter_id.lower(),
            date=date,
        )

        payload: dict[str, object] = {}
        for col_name, col_value in row.items():
            if col_value is not None and col_value != "":
                payload[col_name.lower()] = col_value
        payload["resource_id"] = resource_id.lower()
        payload["meter_id"] = meter_id.lower()

        observations.append(
            AzureActualCostObservation(
                source=source,
                snapshot_id=snapshot_id,
                row_identity=row_identity,
                cost_usd=cost_usd,
                currency=currency,
                payload=MappingProxyType(payload),
            )
        )

    return observations


# ---------------------------------------------------------------------------
# Export completeness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureCostExportCompleteness:
    row_count: int
    total_cost_usd: float
    complete: bool
    observed_meter_categories: frozenset[str] = frozenset()
    failure_reason: str | None = None


def validate_export_completeness(
    observations: Sequence[AzureActualCostObservation],
    *,
    min_row_count: int = 1,
    required_meter_categories: frozenset[str] | set[str] = frozenset(),
    require_resource_ids: bool = False,
) -> AzureCostExportCompleteness:
    row_count = len(observations)
    if row_count < min_row_count:
        return AzureCostExportCompleteness(
            row_count=row_count,
            total_cost_usd=0.0,
            complete=False,
            failure_reason=(f"export has {row_count} rows, minimum required is {min_row_count}"),
        )

    total_cost_usd = 0.0
    observed_categories: set[str] = set()
    for obs in observations:
        total_cost_usd += obs.cost_usd
        payload = obs.payload
        meter_category = payload.get("metercategory")
        if isinstance(meter_category, str) and meter_category.strip():
            observed_categories.add(meter_category.strip())

    effective_required: frozenset[str] = (
        required_meter_categories
        if isinstance(required_meter_categories, frozenset)
        else frozenset(required_meter_categories)
    )
    missing_categories = effective_required - observed_categories
    if missing_categories:
        return AzureCostExportCompleteness(
            row_count=row_count,
            total_cost_usd=total_cost_usd,
            complete=False,
            observed_meter_categories=frozenset(observed_categories),
            failure_reason=(f"required meter categories missing from export: {sorted(missing_categories)}"),
        )

    if require_resource_ids:
        for obs in observations:
            resource_id = obs.payload.get("resource_id")
            if not isinstance(resource_id, str) or not resource_id.strip():
                return AzureCostExportCompleteness(
                    row_count=row_count,
                    total_cost_usd=total_cost_usd,
                    complete=False,
                    failure_reason="export contains a row without a resource_id",
                )

    return AzureCostExportCompleteness(
        row_count=row_count,
        total_cost_usd=total_cost_usd,
        complete=True,
        observed_meter_categories=frozenset(observed_categories),
    )


# ---------------------------------------------------------------------------
# Ingester — the main public entry point for export blobs
# ---------------------------------------------------------------------------


class AzureActualCostExportIngester:
    """Parse and validate Azure Cost Management ActualCost Export blobs.

    Produces :class:`AzureActualCostObservation` rows that downstream
    repository code stores immutably by source/snapshot/row-identity.
    """

    _DEFAULT_SOURCE = "actual-cost-export"

    def __init__(
        self,
        snapshot_id: str,
        *,
        source: str = _DEFAULT_SOURCE,
        column_map: AzureCostExportColumnMap | None = None,
        strict_columns: bool = True,
    ) -> None:
        _require_text("snapshot_id", snapshot_id)
        _require_text("source", source)
        self._snapshot_id = snapshot_id
        self._source = source
        self._column_map = column_map or AzureCostExportColumnMap.default()
        self._strict_columns = strict_columns

    def ingest_csv(self, content: str, *, now: datetime | None = None) -> list[AzureActualCostObservation]:
        effective_now = now or datetime.now()
        return parse_actual_cost_csv(
            content,
            source=self._source,
            snapshot_id=self._snapshot_id,
            now=effective_now,
            column_map=self._column_map,
            strict_columns=self._strict_columns,
        )

    def ingest_raw(
        self,
        records: Sequence[Mapping[str, object]],
        *,
        now: datetime | None = None,
    ) -> list[AzureActualCostObservation]:
        effective_map = self._column_map
        observations: list[AzureActualCostObservation] = []
        for index, record in enumerate(records):
            resource_id = str(record.get(effective_map.resource_id, "")).strip()
            meter_id = str(record.get(effective_map.meter_id, "")).strip()
            raw_cost = record.get(effective_map.cost_in_billing_currency, 0)
            currency = str(record.get(effective_map.billing_currency_code, "")).strip().upper()
            date = str(record.get(effective_map.date, "")).strip()

            if not resource_id:
                raise AzureCostExportParseError(f"Row {index}: resource_id is empty in raw record")

            try:
                cost_usd = float(str(raw_cost))
            except (ValueError, TypeError) as exc:
                raise AzureCostExportParseError(f"Row {index}: cannot parse cost as float: {raw_cost!r}") from exc

            if not currency:
                raise AzureCostExportParseError(f"Row {index}: billing_currency_code is empty")
            if currency != "USD":
                raise AzureCostExportParseError(f"Row {index}: currency must be USD, got {currency!r}")

            row_identity = _compute_row_identity(
                source=self._source,
                snapshot_id=self._snapshot_id,
                row_index=index,
                resource_id=resource_id.lower(),
                meter_id=meter_id.lower(),
                date=date,
            )

            payload: dict[str, object] = {}
            for col_name, col_value in record.items():
                if col_value is not None and col_value != "":
                    payload[str(col_name).lower()] = col_value
            payload["resource_id"] = resource_id.lower()
            payload["meter_id"] = meter_id.lower()

            observations.append(
                AzureActualCostObservation(
                    source=self._source,
                    snapshot_id=self._snapshot_id,
                    row_identity=row_identity,
                    cost_usd=cost_usd,
                    currency=currency,
                    payload=MappingProxyType(payload),
                )
            )
        return observations


# ---------------------------------------------------------------------------
# ActualCostExport — high-level file/string ingestion + storage-ready summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActualCostExportSummary:
    """Immutable ingestion result: parsed observations + storage metadata."""

    source: str
    snapshot_id: str
    ingested_at: datetime
    row_count: int
    total_cost_usd: float
    format: str
    file_name: str
    observations: list[AzureActualCostObservation]

    @property
    def cost_per_resource(self) -> Mapping[str, float]:
        resources: dict[str, float] = {}
        for obs in self.observations:
            resource_id = obs.payload.get("resource_id")
            if isinstance(resource_id, str):
                resources[resource_id] = resources.get(resource_id, 0.0) + obs.cost_usd
        return MappingProxyType(resources)

    @property
    def cost_per_service(self) -> Mapping[str, float]:
        services: dict[str, float] = {}
        for obs in self.observations:
            service_name = str(obs.payload.get("servicename", obs.payload.get("service_name", "Unknown")))
            services[service_name] = services.get(service_name, 0.0) + obs.cost_usd
        return MappingProxyType(services)


class ActualCostExport:
    """High-level Azure ActualCost export ingestion from files or strings.

    Reads CSV/JSON export files from disk, delegates to the lower-level
    parser and ingester primitives in this module, and returns an
    :class:`ActualCostExportSummary` with structured storage-ready data.
    """

    _SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".csv", ".json"})
    _DEFAULT_SOURCE = "actual-cost-export"

    def __init__(
        self,
        *,
        source: str = _DEFAULT_SOURCE,
        snapshot_id: str = "",
        column_map: AzureCostExportColumnMap | None = None,
        strict_columns: bool = True,
    ) -> None:
        _require_text("source", source)
        self._source = source
        self._snapshot_id = snapshot_id
        self._column_map = column_map or AzureCostExportColumnMap.default()
        self._strict_columns = strict_columns

    def ingest(
        self,
        path: Path,
        *,
        now: datetime | None = None,
    ) -> ActualCostExportSummary:
        effective_now = now or datetime.now()
        resolved_path = path.resolve()

        if not resolved_path.is_file():
            raise AzureCostExportError(f"export file does not exist: {resolved_path}")

        suffix = resolved_path.suffix.lower()
        if suffix not in self._SUPPORTED_EXTENSIONS:
            raise AzureCostExportError(
                f"Unsupported export format {suffix!r}. Supported: {sorted(self._SUPPORTED_EXTENSIONS)}"
            )

        content = resolved_path.read_text(encoding="utf-8")

        if suffix == ".csv":
            return self._ingest_csv_content(
                content,
                now=effective_now,
                file_name=resolved_path.name,
            )
        return self._ingest_json_content(
            content,
            now=effective_now,
            file_name=resolved_path.name,
        )

    def ingest_string(
        self,
        content: str,
        *,
        format: str,
        now: datetime | None = None,
    ) -> ActualCostExportSummary:
        effective_now = now or datetime.now()
        normalized_fmt = format.strip().lower()
        if normalized_fmt not in {"csv", "json"}:
            raise AzureCostExportError(f"Unsupported format {format!r}. Supported: csv, json")

        if normalized_fmt == "csv":
            return self._ingest_csv_content(
                content,
                now=effective_now,
                file_name="<string>",
            )
        return self._ingest_json_content(
            content,
            now=effective_now,
            file_name="<string>",
        )

    def ingest_bytes(
        self,
        content: bytes,
        *,
        format: str,
        now: datetime | None = None,
    ) -> ActualCostExportSummary:
        return self.ingest_string(
            content.decode("utf-8"),
            format=format,
            now=now,
        )

    def _ingest_csv_content(
        self,
        content: str,
        *,
        now: datetime,
        file_name: str,
    ) -> ActualCostExportSummary:
        observations = parse_actual_cost_csv(
            content,
            source=self._source,
            snapshot_id=self._snapshot_id,
            now=now,
            column_map=self._column_map,
            strict_columns=self._strict_columns,
        )
        return self._build_summary(observations, now=now, format="csv", file_name=file_name)

    def _ingest_json_content(
        self,
        content: str,
        *,
        now: datetime,
        file_name: str,
    ) -> ActualCostExportSummary:
        try:
            records = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AzureCostExportParseError(f"Failed to parse JSON export: {exc}") from exc

        if not isinstance(records, list):
            raise AzureCostExportParseError(f"JSON export top-level must be a list, got {type(records).__name__}")

        if not records:
            raise AzureCostExportParseError("JSON export has no data rows (empty export)")

        ingester = AzureActualCostExportIngester(
            snapshot_id=self._snapshot_id,
            source=self._source,
            column_map=self._column_map,
            strict_columns=self._strict_columns,
        )
        observations = ingester.ingest_raw(records, now=now)
        return self._build_summary(observations, now=now, format="json", file_name=file_name)

    @staticmethod
    def _build_summary(
        observations: list[AzureActualCostObservation],
        *,
        now: datetime,
        format: str,
        file_name: str,
    ) -> ActualCostExportSummary:
        total_cost = sum(obs.cost_usd for obs in observations)
        source = observations[0].source if observations else ""
        snapshot_id = observations[0].snapshot_id if observations else ""
        return ActualCostExportSummary(
            source=source,
            snapshot_id=snapshot_id,
            ingested_at=now,
            row_count=len(observations),
            total_cost_usd=total_cost,
            format=format,
            file_name=file_name,
            observations=observations,
        )


__all__ = [
    "ACTUALCOST_EXPORT_SCHEMA_V1",
    "ActualCostExport",
    "ActualCostExportSummary",
    "AzureActualCostExportIngester",
    "AzureCostExportColumnMap",
    "AzureCostExportCompleteness",
    "AzureCostExportCompletenessError",
    "AzureCostExportError",
    "AzureCostExportParseError",
    "AzureCostExportSchema",
    "parse_actual_cost_csv",
    "validate_export_completeness",
]
