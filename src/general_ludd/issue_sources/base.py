"""Issue-source contract, registry, and bidirectional sync engine.

KEY CONCEPTS
------------
- :class:`IssueSource` (Protocol): the interface every external issue tracker
  adapter must satisfy (GitHub Issues, Jira, Linear, ...). It exposes a
  ``health`` probe (which must NEVER raise), an issue ``fetch_issues`` reader,
  and two writers — ``update_status`` and ``add_comment`` — used to reflect
  gludd's progress back to the tracker.

- :class:`NormalizedIssue` (TypedDict): the provider-agnostic shape every
  adapter normalizes its native issue payload into. The ``raw`` field preserves
  the untouched provider payload so nothing is lost in normalization.

- :class:`TodoStore` (Protocol): the *injected* internal-todo interface the
  engine writes through. The engine NEVER imports the real database — a caller
  supplies a concrete ``TodoStore`` (production) or a fake (tests). This keeps
  the engine pure and deterministic.

- :class:`IssueSyncEngine`: translates between external issues and gludd's
  internal todo lifecycle in both directions:
    * ``sync_in``  — external issues become internal todos (create/update,
      deduped by ``(source, external_id)``).
    * ``sync_out`` — internal lifecycle changes (ACTIVE/DONE/CANCELLED) are
      pushed back to the tracker via ``IssueSource.update_status``.
  Status mappings in both directions are configurable/overridable.

DESIGN INVARIANTS
-----------------
- The engine depends ONLY on the Protocols above plus an injected store. It
  imports neither the real DB nor any concrete adapter.
- Sync is fault-tolerant: a single failing per-issue update is captured in the
  :class:`SyncReport` and never aborts the rest of the batch.
- Deduplication is by ``(source, external_id)``; the same external issue is
  never turned into two internal todos.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable
from urllib.parse import urlparse

from general_ludd.security.ssrf import host_is_blocked

if TYPE_CHECKING:
    from general_ludd.schemas.todo import TodoStatus

# --------------------------------------------------------------------------- #
# Default status mappings                                                      #
# --------------------------------------------------------------------------- #
#: Internal lifecycle status -> (external status, comment to post). Used by
#: ``sync_out``. Keys are the gludd-internal lifecycle states; both common
#: aliases (ACTIVE/IN_PROGRESS, DONE/COMPLETED) map to the same external state.
DEFAULT_OUTBOUND_STATUS_MAP: dict[str, tuple[str, str | None]] = {
    "ACTIVE": ("In Progress", "gludd is now working this issue"),
    "IN_PROGRESS": ("In Progress", "gludd is now working this issue"),
    "DONE": ("Done", "gludd has completed this issue"),
    "COMPLETED": ("Done", "gludd has completed this issue"),
    "CANCELLED": ("Cancelled", None),
}

#: External tracker status -> internal queue/status. Used by ``sync_in`` when
#: creating/updating an internal todo from an external issue. Adapters and
#: callers can override this entirely; unknown external statuses fall through
#: to the raw external string so information is never silently dropped.
DEFAULT_INBOUND_STATUS_MAP: dict[str, str] = {
    "Open": "QUEUED",
    "To Do": "QUEUED",
    "Backlog": "QUEUED",
    "In Progress": "ACTIVE",
    "Done": "DONE",
    "Closed": "DONE",
    "Cancelled": "CANCELLED",
}


# --------------------------------------------------------------------------- #
# Data shapes                                                                  #
# --------------------------------------------------------------------------- #
class NormalizedIssue(TypedDict):
    """Provider-agnostic representation of an external issue.

    Every adapter normalizes its native payload into this shape before handing
    it to the engine. ``raw`` retains the untouched provider payload.

    Keys:
        external_id: Stable identifier of the issue in the source system
                     (e.g. GitHub issue number as a string, Jira key "PROJ-12").
        source:      Registered name of the source the issue came from. Must
                     match an :class:`IssueSource.name` in the registry.
        title:       Human-readable issue title/summary.
        description: Issue body/description (may be empty).
        status:      External status string as reported by the tracker
                     (e.g. "Open", "In Progress", "Done").
        assignee:    Assignee identifier, or None if unassigned.
        labels:      List of label/tag strings.
        priority:    Priority string (e.g. "High"), or None if unset.
        url:         Canonical web URL of the issue.
        updated_ts:  Last-updated UNIX timestamp, or None if unknown.
        raw:         The untouched provider payload for round-tripping.
    """

    external_id: str
    source: str
    title: str
    description: str
    status: str
    assignee: str | None
    labels: list[str]
    priority: str | None
    url: str
    updated_ts: float | None
    raw: dict[str, Any]


@dataclass
class SyncReport:
    """Outcome of a single ``sync_in`` or ``sync_out`` pass.

    Attributes:
        created: Number of internal todos newly created (sync_in only).
        updated: Number of records changed — internal todos updated (sync_in)
                 or external statuses pushed (sync_out).
        skipped: Number of records that required no action (already in sync,
                 unmapped status, or not linked to the target source).
        errors:  Per-item failures as ``(external_id, message)`` tuples. A
                 non-empty list does NOT mean the whole sync aborted — the
                 remaining items were still processed.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Protocols                                                                    #
# --------------------------------------------------------------------------- #
@runtime_checkable
class SyncSource(Protocol):
    """Contract every external issue-tracker *sync* adapter must satisfy.

    Attributes:
        name:   Registry key for this source instance (e.g. "github-main").
        SYSTEM: The kind of tracker this adapter speaks to (e.g. "github",
                "jira", "linear"). Multiple instances of the same SYSTEM may be
                registered under distinct ``name`` values.
    """

    name: str
    SYSTEM: str

    def health(self) -> dict[str, Any]:
        """Return a health/status dict. MUST NOT raise.

        Implementations must catch their own connection/auth errors and encode
        them in the returned dict (e.g. ``{"ok": False, "error": "..."}``) so a
        health probe can never bring down the caller.
        """
        ...

    def fetch_issues(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch issues matching ``spec`` as a list of :class:`NormalizedIssue`.

        ``spec`` is an adapter-specific query (filters, labels, project, etc.).
        """
        ...

    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, Any]:
        """Set the external issue's status, optionally posting ``comment``."""
        ...

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        """Post a standalone comment to the external issue."""
        ...


@runtime_checkable
class TodoStore(Protocol):
    """Injected interface the engine writes internal todos through.

    This is the seam that keeps the engine decoupled from the real database.
    Production code supplies an adapter backed by the actual todo store; tests
    supply an in-memory fake. The engine never imports the real DB directly.
    """

    def list_linked(self, source: str) -> dict[str, dict[str, Any]]:
        """Return ``{external_id: todo}`` for todos linked to ``source``."""
        ...

    def create_from_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Create an internal todo from a normalized issue; return the todo."""
        ...

    def update_todo(self, todo_id: str, **fields: Any) -> dict[str, Any]:
        """Update fields on the todo identified by ``todo_id``; return it."""
        ...

    def internal_status(self, todo: dict[str, Any]) -> str:
        """Return the gludd-internal lifecycle status for ``todo``."""
        ...


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #
class IssueRegistry:
    """Registry of :class:`SyncSource` instances keyed by ``name``."""

    def __init__(self) -> None:
        self._sources: dict[str, SyncSource] = {}

    def register(self, source: SyncSource) -> None:
        """Register ``source`` under its ``name``.

        Raises:
            ValueError: if a source with the same name is already registered.
        """
        if source.name in self._sources:
            raise ValueError(f"issue source already registered: {source.name!r}")
        self._sources[source.name] = source

    def get(self, name: str) -> SyncSource:
        """Return the source registered under ``name``.

        Raises:
            KeyError: if no source is registered under ``name``.
        """
        try:
            return self._sources[name]
        except KeyError:
            raise KeyError(f"no issue source registered: {name!r}") from None

    def all(self) -> list[SyncSource]:
        """Return a snapshot list of all registered sources."""
        return list(self._sources.values())


# --------------------------------------------------------------------------- #
# Sync engine                                                                  #
# --------------------------------------------------------------------------- #
class IssueSyncEngine:
    """Bidirectional sync between external issues and internal todos.

    The engine is pure/deterministic: it reads/writes only through the injected
    :class:`TodoStore` and the registered :class:`IssueSource` adapters, never
    the real database or a concrete adapter import.

    Args:
        registry:           Registry to resolve source names to adapters.
        todo_store:         Injected internal-todo interface.
        status_map:         Outbound mapping ``internal_status -> (external
                            status, comment)`` for ``sync_out``. Overrides
                            :data:`DEFAULT_OUTBOUND_STATUS_MAP` entirely when
                            provided.
        inbound_status_map: Inbound mapping ``external_status -> internal
                            status`` for ``sync_in``. Overrides
                            :data:`DEFAULT_INBOUND_STATUS_MAP` entirely when
                            provided.
    """

    def __init__(
        self,
        registry: IssueRegistry,
        todo_store: TodoStore,
        status_map: dict[str, tuple[str, str | None]] | None = None,
        inbound_status_map: dict[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self.todo_store = todo_store
        self.status_map: dict[str, tuple[str, str | None]] = (
            dict(DEFAULT_OUTBOUND_STATUS_MAP) if status_map is None else dict(status_map)
        )
        self.inbound_status_map: dict[str, str] = (
            dict(DEFAULT_INBOUND_STATUS_MAP)
            if inbound_status_map is None
            else dict(inbound_status_map)
        )

    # ----------------------------------------------------------------- #
    # mapping helpers                                                    #
    # ----------------------------------------------------------------- #
    def _map_inbound_status(self, external_status: str) -> str:
        """External status -> internal status, falling back to the raw value."""
        return self.inbound_status_map.get(external_status, external_status)

    # ----------------------------------------------------------------- #
    # external -> internal                                               #
    # ----------------------------------------------------------------- #
    def sync_in(
        self, source_name: str, issues: list[dict[str, Any]]
    ) -> SyncReport:
        """Ingest external ``issues`` into internal todos.

        For each issue, deduplicated by ``(source, external_id)``:
        - if no linked todo exists, create one (status mapped inbound);
        - if a linked todo exists and the title or mapped status changed,
          update it;
        - otherwise skip (already in sync).

        A per-issue failure is recorded in :attr:`SyncReport.errors` and never
        aborts the batch. Returns a :class:`SyncReport`.
        """
        report = SyncReport()
        linked = self.todo_store.list_linked(source_name)

        for issue in issues:
            external_id = str(issue.get("external_id", ""))
            try:
                mapped_status = self._map_inbound_status(str(issue.get("status", "")))
                existing = linked.get(external_id)

                if existing is None:
                    # New issue -> create a linked internal todo with mapped status.
                    created = self.todo_store.create_from_issue(
                        {**issue, "status": mapped_status}
                    )
                    # index it so duplicates within the same batch are deduped
                    linked[external_id] = created
                    report.created += 1
                    continue

                # Already linked -> update only if something material changed.
                new_title = issue.get("title", existing.get("title"))
                changed: dict[str, Any] = {}
                if new_title != existing.get("title"):
                    changed["title"] = new_title
                if mapped_status != existing.get("status"):
                    changed["status"] = mapped_status

                if changed:
                    updated = self.todo_store.update_todo(existing["id"], **changed)
                    linked[external_id] = updated
                    report.updated += 1
                else:
                    report.skipped += 1
            except Exception as exc:  # fault-isolate per issue; never abort batch
                report.errors.append((external_id, str(exc)))

        return report

    # ----------------------------------------------------------------- #
    # internal -> external                                               #
    # ----------------------------------------------------------------- #
    def sync_out(
        self, source_name: str, todos: list[dict[str, Any]]
    ) -> SyncReport:
        """Push internal lifecycle changes back to the external tracker.

        For each todo linked to ``source_name`` whose internal lifecycle status
        maps to an external status, call ``source.update_status`` with the
        mapped external status and the configured comment. Todos not linked to
        this source, or whose internal status is unmapped, are skipped.

        A per-todo update failure is captured in :attr:`SyncReport.errors` and
        never aborts the batch. Returns a :class:`SyncReport`.
        """
        report = SyncReport()
        source = self.registry.get(source_name)

        for todo in todos:
            if todo.get("source") != source_name:
                report.skipped += 1
                continue

            external_id = str(todo.get("external_id", ""))
            internal = self.todo_store.internal_status(todo)
            mapping = self.status_map.get(internal)
            if mapping is None:
                report.skipped += 1
                continue

            external_status, comment = mapping
            try:
                source.update_status(external_id, external_status, comment)
                report.updated += 1
            except Exception as exc:  # fault-isolate per todo; never abort batch
                report.errors.append((external_id, str(exc)))

        return report


# --------------------------------------------------------------------------- #
# Adapter base class + canonical record shape (file/REST issue sources)        #
# --------------------------------------------------------------------------- #
# The symbols below back the *adapter* family of issue sources (markdown
# checklist, CSV/Excel, and ingest). Those adapters subclass :class:`IssueSource`
# (a concrete base, distinct from the structural :class:`SyncSource` Protocol the
# sync engine consumes), emit :class:`IssueRecord` payloads via
# :func:`new_issue_record`, and apply local lifecycle changes through
# :class:`Transition` write-backs.


class Transition(enum.Enum):
    """Local lifecycle change an adapter mirrors outward via ``write_back``.

    ``CLAIM`` marks an external item as picked up / in progress; ``DONE`` marks
    it complete. The string ``value`` is the wire/word an adapter may write.
    """

    CLAIM = "claim"
    DONE = "done"


class IssueRecord(TypedDict):
    """Canonical record an adapter's ``fetch`` yields.

    Distinct from :class:`NormalizedIssue` (the sync-engine shape): this record
    uses ``body``/``updated_at`` and is the input to
    :func:`~general_ludd.issue_sources.ingest.record_to_todo`.

    Keys:
        external_id: Stable id of the item in the source.
        source:      Registered source name (set by :func:`new_issue_record`).
        title:       Human-readable title.
        body:        Item body/description (may be empty).
        status:      External status string (mapped via :func:`map_external_status`).
        priority:    Priority string, or None.
        assignee:    Assignee identifier, or None.
        labels:      Label/tag strings.
        url:         Canonical/locator URL.
        updated_at:  Last-updated UNIX timestamp, or None.
        raw:         Untouched provider payload.
    """

    external_id: str
    source: str
    title: str
    body: str
    status: str
    priority: str | None
    assignee: str | None
    labels: list[str]
    url: str
    updated_at: float | None
    raw: dict[str, Any]


@runtime_checkable
class SourceTransport(Protocol):
    """Injectable transport a network-backed adapter performs requests through.

    A callable issuing one time-bound request. File-based adapters (markdown,
    CSV/Excel) take no transport; it exists for REST adapters and tests.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, str] | None = ...,
        json: dict[str, Any] | None = ...,
        timeout: float = ...,
    ) -> Any: ...


#: External status word (lower-cased) -> internal :class:`TodoStatus` value.
#: Unknown words fall through to ``BACKLOG`` (see :func:`map_external_status`).
_EXTERNAL_STATUS_MAP: dict[str, str] = {
    "open": "backlog",
    "new": "backlog",
    "to do": "backlog",
    "todo": "backlog",
    "backlog": "backlog",
    "queued": "queued",
    "in progress": "active",
    "in-progress": "active",
    "active": "active",
    "doing": "active",
    "started": "active",
    "done": "complete",
    "closed": "complete",
    "complete": "complete",
    "completed": "complete",
    "resolved": "complete",
    "fixed": "complete",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "wontfix": "cancelled",
    "blocked": "blocked",
}


def map_external_status(external_status: str) -> TodoStatus:
    """Map an external status word onto an internal :class:`TodoStatus`.

    Case-insensitive; unrecognised words default to ``TodoStatus.BACKLOG`` so an
    unknown external status is never silently dropped (it becomes a backlog item).
    """
    from general_ludd.schemas.todo import TodoStatus

    key = (external_status or "").strip().lower()
    return TodoStatus(_EXTERNAL_STATUS_MAP.get(key, "backlog"))


def parse_iso_ts(value: str | None) -> float | None:
    """Parse an ISO-8601 timestamp into a UNIX epoch float, or None.

    Returns None for a falsy/None input or a value that does not parse. A
    trailing ``Z`` (UTC) is accepted.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def new_issue_record(
    *,
    external_id: str,
    title: str,
    body: str = "",
    status: str = "open",
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    url: str = "",
    updated_at: float | None = None,
    source: str = "",
    raw: dict[str, Any] | None = None,
) -> IssueRecord:
    """Construct a fully-populated :class:`IssueRecord` with sane defaults."""
    return IssueRecord(
        external_id=external_id,
        source=source,
        title=title,
        body=body,
        status=status,
        priority=priority,
        assignee=assignee,
        labels=list(labels) if labels else [],
        url=url,
        updated_at=updated_at,
        raw=dict(raw) if raw else {},
    )


def _is_internal_host(host: str) -> bool:
    """Return True for a literal internal/loopback host (no DNS resolution).

    Delegates entirely to the canonical
    :func:`general_ludd.security.ssrf.host_is_blocked` so this guard's
    classification (previously a local reimplementation missing the
    ``not is_global`` catch for CGNAT/TEST-NET ranges and the cloud-metadata
    NAME/IP blocklist) can never drift from the single source of truth.
    """
    return host_is_blocked(host)


class IssueSource:
    """Concrete base for adapter-style issue sources (file or REST).

    Subclasses (markdown checklist, CSV/Excel, REST trackers) set a ``SOURCE``
    class attribute, override :meth:`fetch` and :meth:`write_back`, and call
    ``super().__init__(config, transport, require_base_url=...)``. When a
    ``base_url`` is required (REST sources), the base validates it is not an
    internal/loopback host (literal-host SSRF guard, no DNS).
    """

    #: Registered source kind; subclasses override.
    SOURCE: str = "issue_source"

    def __init__(
        self,
        config: dict[str, Any],
        transport: SourceTransport | None = None,
        *,
        require_base_url: bool = True,
    ) -> None:
        self.config: dict[str, Any] = dict(config)
        self.transport = transport
        base_url = config.get("base_url")
        self.base_url: str | None = str(base_url) if base_url else None
        if require_base_url and not self.base_url:
            raise ValueError("config['base_url'] is required for this source")
        # SSRF guard runs whenever a base_url is PRESENT, regardless of
        # ``require_base_url`` (which now only governs the missing-url error).
        self._guard_base_url()

    def _guard_base_url(self) -> None:
        """Reject a ``base_url`` pointing at an internal/loopback host.

        Literal-host SSRF guard (no DNS resolution). A no-op when ``base_url``
        is unset; otherwise raises ``ValueError`` on an internal host. Safe to
        re-invoke per-request for defense-in-depth against post-construction
        ``base_url`` mutation.
        """
        if not self.base_url:
            return
        host = urlparse(self.base_url).hostname or ""
        if _is_internal_host(host):
            raise ValueError(f"refusing internal base_url host: {host!r}")

    @property
    def name(self) -> str:
        """Registered name of this source (defaults to ``SOURCE``)."""
        return str(self.config.get("name") or self.SOURCE)

    def fetch(self, spec: dict[str, Any] | None = None) -> list[IssueRecord]:
        """Return the source's issues as :class:`IssueRecord` payloads."""
        raise NotImplementedError

    def write_back(self, external_id: str, transition: Transition) -> bool:
        """Apply ``transition`` to ``external_id``; return True on success."""
        raise NotImplementedError
