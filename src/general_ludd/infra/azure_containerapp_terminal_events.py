"""Bounded terminal-event diagnostics for one exact Azure revision."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from general_ludd.infra.azure_containerapp_runtime_state import _revision_state
from general_ludd.infra.azure_containerapp_sdk import AzureContainerAppsSDKReadError

_POLL_ATTEMPTS = 4
_POLL_SECONDS = 5.0


def _system_event_counts(document: object) -> tuple[int, int, int, int, int, int]:
    """Return bounded counters while discarding provider-controlled values."""
    if not isinstance(document, Mapping):
        return 0, 0, 0, 0, 0, 0
    values: list[int] = []
    for name in (
        "eventCount",
        "scopedEventCount",
        "classifiedEventCount",
        "errorEventCount",
        "warningEventCount",
        "unclassifiedErrorCount",
    ):
        value = document.get(name)
        values.append(
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 300
            else 0
        )
    return values[0], values[1], values[2], values[3], values[4], values[5]


@dataclass(frozen=True, slots=True)
class _EventAttempt:
    """Sanitized result of polling all available diagnostic sources once."""

    reasons: frozenset[str]
    reader_available: bool
    unclassified_error: bool


@dataclass(slots=True)
class AzureTerminalEventReader:
    """Read bounded app and environment events for one exact revision."""

    app_transport: Any
    lifecycle_transport: Any
    progress_sink: Callable[[str], None]
    sleep: Callable[[float], None]

    def read(self, token: str, revision_name: str | None) -> tuple[str, ...]:
        """Return fixed reason classes after a short, visible retry window."""
        if revision_name is None:
            self.progress_sink(
                "azure_containerapp_system_event_poll phase=readiness "
                "source=combined state=supplementary_unavailable "
                "reason=revision_identity_missing"
            )
            return ()
        readers = self._readers()
        saw_reader = False
        saw_unclassified_error = False
        for attempt in range(1, _POLL_ATTEMPTS + 1):
            result = self._read_attempt(token, revision_name, readers, attempt)
            saw_reader = saw_reader or result.reader_available
            saw_unclassified_error = (
                saw_unclassified_error or result.unclassified_error
            )
            if result.reasons:
                return tuple(sorted(result.reasons))
            if not saw_reader:
                return ()
            self._wait_before_retry(attempt)
        return ("system_error_unclassified",) if saw_unclassified_error else ()

    def _readers(self) -> tuple[tuple[str, object], ...]:
        """Resolve optional transports once for a deterministic retry window."""
        return (
            (
                "app",
                getattr(self.app_transport, "get_system_event_reason_classes", None),
            ),
            (
                "environment",
                getattr(
                    self.lifecycle_transport,
                    "get_environment_system_event_reason_classes",
                    None,
                ),
            ),
        )

    def _read_attempt(
        self,
        token: str,
        revision_name: str,
        readers: tuple[tuple[str, object], ...],
        attempt: int,
    ) -> _EventAttempt:
        """Read each available source once and sanitize its evidence."""
        merged: set[str] = set()
        reader_available = False
        unclassified_error = False
        for source, reader in readers:
            if not callable(reader):
                continue
            reader_available = True
            try:
                document = reader(token, revision_name)
            except AzureContainerAppsSDKReadError:
                self._emit_unavailable(source, attempt)
                continue
            event_reasons = _revision_state({"properties": document}).reasons
            merged.update(event_reasons)
            counts = _system_event_counts(document)
            unclassified_error = unclassified_error or bool(counts[-1])
            self._emit_available(source, attempt, event_reasons, counts)
        return _EventAttempt(
            reasons=frozenset(merged),
            reader_available=reader_available,
            unclassified_error=unclassified_error,
        )

    def _emit_unavailable(self, source: str, attempt: int) -> None:
        """Expose a fixed SDK failure without leaking provider payloads."""
        self.progress_sink(
            "azure_containerapp_system_event_poll phase=readiness "
            f"source={source} state=supplementary_unavailable "
            f"attempt={attempt} reason=sdk_read_failed"
        )

    def _emit_available(
        self,
        source: str,
        attempt: int,
        reasons: tuple[str, ...],
        counts: tuple[int, int, int, int, int, int],
    ) -> None:
        """Expose sanitized counters and fixed reason classes."""
        reason_classes = ",".join(reasons) if reasons else "none"
        self.progress_sink(
            "azure_containerapp_system_event_poll phase=readiness "
            f"source={source} state=available attempt={attempt} "
            f"event_count={counts[0]} scoped_event_count={counts[1]} "
            f"classified_event_count={counts[2]} "
            f"error_event_count={counts[3]} warning_event_count={counts[4]} "
            f"unclassified_error_count={counts[5]} "
            f"reason_classes={reason_classes}"
        )

    def _wait_before_retry(self, attempt: int) -> None:
        """Emit a heartbeat before each bounded diagnostic delay."""
        if attempt >= _POLL_ATTEMPTS:
            return
        self.progress_sink(
            "azure_containerapp_system_event_wait phase=readiness "
            f"state=heartbeat attempt={attempt} "
            f"delay_seconds={int(_POLL_SECONDS)}"
        )
        self.sleep(_POLL_SECONDS)


__all__ = ("AzureTerminalEventReader",)
