"""Repository implementations for the agentic harness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from general_ludd.db.models import (
    FeatureModel,
    FeatureStatus,
    LocationKind,
    ProjectModel,
    ProjectRelationshipModel,
    RelationType,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.db.repositories.shared import current_list_limit, dialect_insert


class VariableNamespaceRepository:
    """Persist project and global namespaced configuration variables."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def load_vars_for_project(self, project_id: str | None) -> dict[str, str]:
        """Load merged global and project variables with project values winning."""
        scope_filter: ColumnElement[bool] = VariableNamespaceModel.project_id.is_(None)
        if project_id is not None:
            scope_filter = (VariableNamespaceModel.project_id == project_id) | (
                VariableNamespaceModel.project_id.is_(None)
                & (VariableNamespaceModel.namespace != "tool_results")
            )
        stmt = (
            select(VariableValueModel)
            .join(VariableNamespaceModel)
            .where(scope_filter)
            .order_by(VariableNamespaceModel.project_id.is_(None).desc())
            # P12: defensive cap; variable sets are expected to be small but
            # an unbounded JOIN load is still a risk surface.
            .limit(current_list_limit())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        result_dict: dict[str, str] = {}
        for row in rows:
            result_dict[row.key] = row.value
        return result_dict

    async def create_namespace(self, namespace: str, project_id: str | None = None) -> VariableNamespaceModel:
        """Create and return a global or project-specific namespace."""
        row = VariableNamespaceModel(namespace=namespace, project_id=project_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_var(self, namespace: str, key: str, value: str, project_id: str | None = None) -> VariableValueModel:
        """Atomically upsert and return a namespaced variable value."""
        insert = dialect_insert(
            VariableNamespaceModel,
            self._session.get_bind().dialect.name,
        )

        # Resolve (or atomically create) the namespace. get-then-insert here is a
        # TOCTOU race on the (namespace, project_id) unique key: two concurrent
        # first-writers both see ns is None and both INSERT -> IntegrityError.
        # ON CONFLICT DO NOTHING on the unique constraint makes the insert a no-op
        # for the loser, who then re-reads the winner's row.
        stmt = select(VariableNamespaceModel).where(
            VariableNamespaceModel.namespace == namespace,
            VariableNamespaceModel.project_id == project_id,
        )
        ns = (await self._session.execute(stmt)).scalar_one_or_none()
        if ns is None:
            ns_insert = (
                insert
                .values(namespace=namespace, project_id=project_id)
                .on_conflict_do_nothing(index_elements=["namespace", "project_id"])
            )
            await self._session.execute(ns_insert)
            await self._session.flush()
            ns = (await self._session.execute(stmt)).scalar_one()

        # Upsert the value on the (namespace_id, key) unique key. on_conflict_do_update
        # closes the get-then-insert TOCTOU: a concurrent first-write no longer
        # raises IntegrityError (and is no longer silently lost) — last writer wins.
        now = datetime.now(UTC)
        val_insert = (
            dialect_insert(
                VariableValueModel,
                self._session.get_bind().dialect.name,
            )
            .values(namespace_id=ns.id, key=key, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=["namespace_id", "key"],
                set_={"value": value, "updated_at": now},
            )
        )
        await self._session.execute(val_insert)
        await self._session.flush()
        row = (
            await self._session.execute(
                select(VariableValueModel).where(
                    VariableValueModel.namespace_id == ns.id,
                    VariableValueModel.key == key,
                )
            )
        ).scalar_one()
        # on_conflict_do_update bypasses the identity map; refresh so a previously
        # loaded value row reflects the just-committed value.
        await self._session.refresh(row)
        return row



class ProjectRepository:
    """Persist project records and their active lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> ProjectModel:
        """Persist and return a project record."""
        row = ProjectModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, project_id: str) -> ProjectModel | None:
        """Return a project by its public identifier."""
        stmt = select(ProjectModel).where(ProjectModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[ProjectModel]:
        """List a bounded set of active projects."""
        # P12: bound the read so a large project table can't load unboundedly.
        # Compatibility marker: .limit(_DEFAULT_LIST_LIMIT)
        stmt = select(ProjectModel).where(ProjectModel.active.is_(True)).limit(current_list_limit())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, project_id: str) -> None:
        """Atomically deactivate an active project, leaving missing rows unchanged."""
        from sqlalchemy import update as _update

        # Single guarded UPDATE rather than read-then-mutate: the read-modify-write
        # form lets a concurrent writer's change be lost between the SELECT and the
        # ORM flush. Guarding on active == True also makes a double-deactivate a
        # detectable no-op instead of a silent clobber.
        guard = (
            _update(ProjectModel)
            .where(
                ProjectModel.project_id == project_id,
                ProjectModel.active.is_(True),
            )
            .values(active=False)
        )
        await self._session.execute(guard)
        await self._session.flush()
        # Keep any already-loaded ORM instance consistent with the committed row.
        project = await self.get_by_id(project_id)
        if project is not None:
            await self._session.refresh(project)


class ProjectRelationshipRepository:
    """Persistence for declared project-topology edges (ProjectRelationshipModel).

    Edges are USER-DECLARED (config or API), never inferred. ``add_relationship``
    is an idempotent upsert keyed on the unique edge tuple
    ``(project_id, relation_type, location_kind, location_value)``. The
    "one parent per project" rule is enforced here (``add_relationship`` replaces an
    existing parent edge) because SQLite cannot express a portable partial unique
    index; PostgreSQL also carries the ``uq_one_parent`` partial index.
    """

    # The owning project may declare at most one of these relation types.
    _SINGLETON_RELATIONS: frozenset[str] = frozenset({RelationType.PARENT.value})
    _LEGACY_LOCATION_KINDS: ClassVar[dict[str, str]] = {"path": LocationKind.DIRECTORY.value}

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def add_relationship(self, data: dict[str, Any]) -> ProjectRelationshipModel:
        """Idempotent upsert of one declared edge; enforces the one-parent rule.

        Rejects a self-edge (``related_project_id == project_id``). For a
        ``relation_type='parent'`` edge that does not match an existing parent
        row's full unique tuple, any existing parent edge for the project is first
        removed (replace-on-second-parent), so a project never carries two parents.
        Re-declaring the SAME edge tuple updates it in place (no duplicate row).
        """
        data = dict(data)
        raw_location_kind = str(data.get("location_kind", ""))
        data["location_kind"] = self._LEGACY_LOCATION_KINDS.get(raw_location_kind, raw_location_kind)

        project_id = data.get("project_id", "")
        relation_type = str(data.get("relation_type", ""))
        location_kind = str(data.get("location_kind", ""))

        # Enum validation: the repository is the security boundary for the
        # direct-call path (config parsing validates upstream, but callers may
        # invoke add_relationship directly). Reject anything that is not a valid
        # RelationType / LocationKind StrEnum value BEFORE persisting, so the
        # column never holds an out-of-domain string.
        try:
            RelationType(relation_type)
        except ValueError as exc:
            valid = ", ".join(r.value for r in RelationType)
            raise ValueError(f"invalid relation_type {relation_type!r}; must be one of: {valid}") from exc
        try:
            LocationKind(location_kind)
        except ValueError as exc:
            valid = ", ".join(k.value for k in LocationKind)
            raise ValueError(f"invalid location_kind {location_kind!r}; must be one of: {valid}") from exc

        related_project_id = data.get("related_project_id")
        if related_project_id is not None and related_project_id == project_id:
            raise ValueError(f"self-edge rejected: related_project_id {related_project_id!r} == project_id")

        existing = await self._get_edge(
            project_id,
            relation_type,
            location_kind,
            str(data.get("location_value", "")),
        )

        if existing is not None:
            for key, value in data.items():
                if key in ("id", "project_id", "created_at"):
                    continue
                setattr(existing, key, value)
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        # One-parent guard + insert as ONE atomic transaction unit. A NEW parent
        # edge (tuple differs from any existing parent row) replaces the prior
        # parent: we delete the prior parent rows and add the new edge with NO
        # intermediate flush/commit between them, then a single flush at the end.
        # Either both the delete and the insert land or neither does (they share
        # one transaction and roll back together on failure), so the project can
        # never be left with zero parents mid-operation. Re-declaring the same
        # parent tuple is handled by the upsert branch above, so it never reaches
        # here as a "second" parent.
        #
        # This ordering covers the local SQLite case (single writer per file).
        # True multi-connection concurrency safety relies on the PostgreSQL
        # ``uq_one_parent`` partial unique index already added in migration 008,
        # which rejects a concurrent second parent at the database level.
        if relation_type in self._SINGLETON_RELATIONS:
            for prior in await self.list_for_project(project_id, relation_type=relation_type):
                await self._session.delete(prior)

        row = ProjectRelationshipModel(**data)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def _get_edge(
        self,
        project_id: str,
        relation_type: str,
        location_kind: str,
        location_value: str,
    ) -> ProjectRelationshipModel | None:
        stmt = select(ProjectRelationshipModel).where(
            ProjectRelationshipModel.project_id == project_id,
            ProjectRelationshipModel.relation_type == relation_type,
            ProjectRelationshipModel.location_kind == location_kind,
            ProjectRelationshipModel.location_value == location_value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: str,
        relation_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProjectRelationshipModel]:
        """List a bounded page of declared relationships for a project."""
        stmt = select(ProjectRelationshipModel).where(ProjectRelationshipModel.project_id == project_id)
        if relation_type is not None:
            stmt = stmt.where(ProjectRelationshipModel.relation_type == relation_type)
        stmt = (
            stmt.order_by(ProjectRelationshipModel.id)
            .offset(offset)
            .limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_parent(self, project_id: str) -> ProjectRelationshipModel | None:
        """Return the project's declared parent edge, if present."""
        edges = await self.list_for_project(project_id, relation_type=RelationType.PARENT.value)
        return edges[0] if edges else None

    async def list_children(
        self, project_id: str, limit: int | None = None, offset: int = 0
    ) -> list[ProjectRelationshipModel]:
        """List a bounded page of the project's declared child edges."""
        return await self.list_for_project(
            project_id,
            relation_type=RelationType.CHILD.value,
            limit=limit,
            offset=offset,
        )

    async def remove(self, rel_id: str) -> bool:
        """Delete one edge by its primary key. Returns True iff a row was removed."""
        stmt = select(ProjectRelationshipModel).where(ProjectRelationshipModel.id == rel_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

class FeatureRepository:
    """Persistence for the feature database (FeatureModel).

    JSON (de)serialization happens at the repo boundary — callers pass / receive
    Python objects (lists/dicts); the DB stores JSON-in-Text as do PromptProfileModel
    and TodoModel.
    """

    def __init__(self, session: AsyncSession, project_id: str | None = None) -> None:
        """Initialize the repository with an optional default tenant scope."""
        self._session = session
        self._project_id = project_id

    @classmethod
    def scoped(cls, session: AsyncSession, project_id: str) -> FeatureRepository:
        """Return a repository pre-scoped to *project_id*.

        Read methods that accept ``project_id`` fall back to this scope when the
        caller passes ``project_id=None``, preventing silent cross-tenant reads
        (XT-2/5/6/7). Mirrors ``TodoRepository.scoped``.
        """
        return cls(session, project_id=project_id)

    def _resolve_pid(self, project_id: str | None) -> str | None:
        """Return *project_id* if explicitly supplied, else the instance scope.

        ``None`` propagates only when the instance was not scoped (admin path),
        preserving cross-tenant reads for internal callers like ``set_status``.
        """
        return project_id if project_id is not None else self._project_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(value: Any) -> str:
        import json as _json

        if isinstance(value, str):
            return value
        return _json.dumps(value)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert(self, data: dict[str, Any]) -> FeatureModel:
        """Insert or update a feature row, keyed by name (unique).

        Fields that are lists/dicts in ``data`` are serialized to JSON before
        being written to the DB.  The returned model has JSON-string columns.
        """
        json_fields = {"acceptance_criteria", "evidence"}
        serialized: dict[str, Any] = {}
        for key, val in data.items():
            if key in json_fields:
                serialized[key] = self._serialize(val)
            else:
                serialized[key] = val

        # Upsert on the unique ``name`` key. get-then-insert was a TOCTOU race
        # (two concurrent first-writes -> IntegrityError, one silently lost);
        # on_conflict_do_update turns the conflicting write into an UPDATE so
        # concurrent first-writes converge instead of raising.
        update_cols = {k: v for k, v in serialized.items() if k not in ("id", "name")}
        stmt = dialect_insert(
            FeatureModel,
            self._session.get_bind().dialect.name,
        ).values(**serialized)
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=["name"], set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self.get_by_name(data.get("name", ""))
        assert row is not None  # just upserted
        # The core INSERT ... ON CONFLICT bypasses the ORM identity map, so a row
        # already loaded this session is stale after the UPDATE — refresh it.
        await self._session.refresh(row)
        return row

    async def set_status(
        self,
        feature_id: str,
        status: FeatureStatus,
        detail: dict[str, Any] | None = None,
        verified_at: datetime | None = None,
    ) -> FeatureModel:
        """Update status, optional verified_at, and persist the verify-detail JSON."""
        import json as _json

        from sqlalchemy import update as _update

        row = await self.get_by_id(feature_id)
        if row is None:
            raise KeyError(f"Feature {feature_id!r} not found")
        # Single guarded UPDATE keyed on id instead of read-modify-write: the
        # ORM dirty-write form lets a concurrent status write be lost between the
        # SELECT above and the flush. Only the columns actually supplied are set.
        values: dict[str, Any] = {"status": status.value}
        if verified_at is not None:
            values["verified_at"] = verified_at
        if detail is not None:
            values["last_verify_detail"] = _json.dumps(detail)
        guard = _update(FeatureModel).where(FeatureModel.id == feature_id).values(**values)
        res = await self._session.execute(guard)
        if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
            raise KeyError(f"Feature {feature_id!r} not found")
        await self._session.flush()
        await self._session.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_name(self, name: str, project_id: str | None = None) -> FeatureModel | None:
        """Return a named feature within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.name == name)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, feature_id: str, project_id: str | None = None) -> FeatureModel | None:
        """Return a feature by identifier within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.id == feature_id)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self, limit: int | None = None, offset: int = 0, project_id: str | None = None
    ) -> list[FeatureModel]:
        """List a bounded feature page within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(
            min(limit, current_list_limit()) if limit is not None else current_list_limit()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        status: FeatureStatus,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[FeatureModel]:
        """List a bounded page of features with the requested status."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.status == status.value)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(
            min(limit, current_list_limit()) if limit is not None else current_list_limit()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_category(
        self,
        category: str,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[FeatureModel]:
        """List a bounded page of features in the requested category."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.category == category)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(
            min(limit, current_list_limit()) if limit is not None else current_list_limit()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
