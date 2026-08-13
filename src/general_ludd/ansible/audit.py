"""Structured audit logging for playbook execution (OpenShell P1 transfer).

Mirrors OpenShell's audit trail: every policy-denied outbound request,
credential access, and protected-path write is recorded as a structured JSON
audit event so operators can detect exfiltration attempts from the audit log.

The logger is deliberately **fail-open**: an audit sink that raises (disk full,
broken pipe) must never abort the playbook it is observing — the security value
of the run continuing outweighs a single dropped audit line, and the in-memory
buffer still retains every event for later inspection via :meth:`flush`.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The three audit event categories transferred from OpenShell.
NETWORK_DENY = "network_deny"
CREDENTIAL_ACCESS = "credential_access"
PATH_BLOCKED = "path_blocked"


@dataclass
class AuditEvent:
    """A single structured audit event emitted during playbook execution.

    ``detail`` carries the event-specific payload:
      - ``network_deny``      -> ``{"method", "url", "policy"}``
      - ``credential_access`` -> ``{"secret_name"}``
      - ``path_blocked``      -> ``{"path"}``
    """

    event_type: str
    module: str
    detail: dict[str, Any]
    playbook: str
    timestamp: float = field(default_factory=time.time)
    sandbox_id: str | None = None

    def __post_init__(self) -> None:
        """Detach the stored payload from caller-owned mutable state."""
        self.detail = dict(self.detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "module": self.module,
            "detail": self.detail,
            "playbook": self.playbook,
            "timestamp": self.timestamp,
            "sandbox_id": self.sandbox_id,
        }

    def to_json(self) -> str:
        """Serialize to a single JSON line (sort_keys for stable audit diffs)."""
        return json.dumps(self.to_dict(), sort_keys=True, default=str)


class PlaybookAuditLogger:
    """Collects and emits structured audit events for one playbook run.

    Events are buffered in memory (retrievable via :meth:`flush` for test
    assertions and post-run inspection) AND written to an optional ``sink``
    (default: the module logger) as JSON lines. Sink failures are swallowed —
    the logger is fail-open by design (see module docstring).
    """

    def __init__(
        self,
        playbook: str,
        sandbox_id: str | None = None,
        sink: Callable[[str], None] | None = None,
    ) -> None:
        self._playbook = playbook
        self._sandbox_id = sandbox_id
        self._sink = sink if sink is not None else self._default_sink
        self._events: list[AuditEvent] = []

    @staticmethod
    def _default_sink(line: str) -> None:
        logger.info("audit %s", line)

    def emit(self, event: AuditEvent) -> None:
        """Buffer *event* and write it to the sink. Never raises (fail-open)."""
        self._events.append(event)
        try:
            self._sink(event.to_json())
        except Exception as exc:  # fail-open: a bad sink must not abort the run
            logger.warning(
                "audit sink failed for event type=%s playbook=%s: %s",
                event.event_type,
                self._playbook,
                exc,
            )

    def _emit(self, event_type: str, module: str, detail: dict[str, Any]) -> None:
        self.emit(
            AuditEvent(
                event_type=event_type,
                module=module,
                detail=detail,
                playbook=self._playbook,
                timestamp=time.time(),
                sandbox_id=self._sandbox_id,
            )
        )

    def network_deny(
        self, module: str, method: str, url: str, policy: str
    ) -> None:
        """Log a network policy denial (outbound request blocked)."""
        self._emit(
            NETWORK_DENY,
            module,
            {"method": method, "url": url, "policy": policy},
        )

    def credential_access(self, module: str, secret_name: str) -> None:
        """Log a task reading an OpenBao / env secret."""
        self._emit(
            CREDENTIAL_ACCESS,
            module,
            {"secret_name": secret_name},
        )

    def path_blocked(self, module: str, path: str) -> None:
        """Log a task attempting to write to a protected path."""
        self._emit(
            PATH_BLOCKED,
            module,
            {"path": path},
        )

    def flush(self) -> list[AuditEvent]:
        """Return every buffered event (for assertions / post-run inspection)."""
        return list(self._events)
