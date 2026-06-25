"""Unit tests for the project-relationship edge graph (phase 1).

Covers the ``ProjectRelationshipModel`` ORM model, the
``ProjectRelationshipRepository`` (add/list/get_parent/list_children/remove,
one-parent guard, self-edge rejection), the FK cascade / SET NULL behavior, and
config-driven seeding (``parse_relationships`` + ``persist_relationships_from_config``).

Uses SQLite in-memory with async sessions via aiosqlite (PRAGMA foreign_keys=ON
so the CASCADE / SET NULL FKs are actually enforced), mirroring
``tests/unit/test_db_models.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    Base,
    LocationKind,
    ProjectModel,
    ProjectRelationshipModel,
    RelationType,
)
from general_ludd.db.repository import (
    ProjectRelationshipRepository,
    ProjectRepository,
)
from general_ludd.projects.manager import (
    normalize_relationship_config,
    parse_relationships,
    persist_relationships_from_config,
    seed_from_config,
)

# asyncio runs in AUTO mode (see pyproject.toml), so async tests need no explicit
# mark; the sync config-parsing tests run as plain functions.


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


async def _make_project(
    session: AsyncSession,
    project_id: str,
    name: str = "",
    workspace_path: str = "",
) -> ProjectModel:
    proj_repo = ProjectRepository(session)
    return await proj_repo.create(
        {
            "project_id": project_id,
            "name": name or project_id,
            "workspace_path": workspace_path,
        }
    )


class TestProjectRelationshipModel:
    async def test_create_edge_round_trip(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-aaa", "payments-api")
        edge = ProjectRelationshipModel(
            project_id="proj-aaa",
            relation_type=RelationType.PARENT.value,
            location_kind=LocationKind.GLUDD_PROJECT_NAME.value,
            location_value="acme-platform",
            controlled_by_gludd=True,
            interface_hint="GET /oauth/introspect",
        )
        async_session.add(edge)
        await async_session.flush()
        assert edge.id.startswith("rel-")
        assert edge.interface_contract == "{}"
        assert edge.controlled_by_gludd is True
        assert edge.created_at is not None

    async def test_defaults(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-bbb")
        edge = ProjectRelationshipModel(
            project_id="proj-bbb",
            relation_type="external",
            location_kind="url",
            location_value="https://api.stripe.com",
        )
        async_session.add(edge)
        await async_session.flush()
        assert edge.controlled_by_gludd is False
        assert edge.related_project_id is None
        assert edge.interface_hint is None


class TestProjectRelationshipRepository:
    async def test_add_and_list_for_project(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "directory",
                "location_value": "./services/ledger",
            }
        )
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "external",
                "location_kind": "url",
                "location_value": "https://api.stripe.com",
            }
        )
        edges = await repo.list_for_project("proj-a")
        assert len(edges) == 2
        children = await repo.list_for_project("proj-a", relation_type="child")
        assert len(children) == 1
        assert children[0].location_value == "./services/ledger"

    async def test_add_is_idempotent_on_edge_tuple(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        first = await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "directory",
                "location_value": "./svc",
                "interface_hint": "v1",
            }
        )
        # Re-declare the SAME edge tuple with an updated hint -> update in place.
        second = await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "directory",
                "location_value": "./svc",
                "interface_hint": "v2",
            }
        )
        assert first.id == second.id
        edges = await repo.list_for_project("proj-a")
        assert len(edges) == 1
        assert edges[0].interface_hint == "v2"

    async def test_get_parent_and_list_children(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "parent",
                "location_kind": "gludd_project_name",
                "location_value": "acme-platform",
            }
        )
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "gludd_project_name",
                "location_value": "ledger",
            }
        )
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "gludd_project_name",
                "location_value": "notifications",
            }
        )
        parent = await repo.get_parent("proj-a")
        assert parent is not None
        assert parent.location_value == "acme-platform"
        children = await repo.list_children("proj-a")
        assert {c.location_value for c in children} == {"ledger", "notifications"}

    async def test_get_parent_none_when_absent(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        assert await repo.get_parent("proj-a") is None

    async def test_one_parent_guard_replaces_second_parent(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "parent",
                "location_kind": "gludd_project_name",
                "location_value": "platform-one",
            }
        )
        # A SECOND, different parent must REPLACE the first (one-parent rule).
        await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "parent",
                "location_kind": "gludd_project_name",
                "location_value": "platform-two",
            }
        )
        parents = await repo.list_for_project("proj-a", relation_type="parent")
        assert len(parents) == 1
        assert parents[0].location_value == "platform-two"

    async def test_self_edge_rejected(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        with pytest.raises(ValueError, match="self-edge"):
            await repo.add_relationship(
                {
                    "project_id": "proj-a",
                    "relation_type": "sibling",
                    "location_kind": "gludd_project_name",
                    "location_value": "a",
                    "related_project_id": "proj-a",
                }
            )

    async def test_remove(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a", "a")
        repo = ProjectRelationshipRepository(async_session)
        row = await repo.add_relationship(
            {
                "project_id": "proj-a",
                "relation_type": "child",
                "location_kind": "directory",
                "location_value": "./svc",
            }
        )
        assert await repo.remove(row.id) is True
        assert await repo.list_for_project("proj-a") == []
        # Removing a non-existent edge is a no-op returning False.
        assert await repo.remove("rel-doesnotexist") is False


class TestForeignKeyBehavior:
    async def test_cascade_on_owning_project_delete(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-own", "owner")
        repo = ProjectRelationshipRepository(async_session)
        await repo.add_relationship(
            {
                "project_id": "proj-own",
                "relation_type": "child",
                "location_kind": "directory",
                "location_value": "./svc",
            }
        )
        # Delete the owning project -> its edges are CASCADE-deleted.
        owner = await async_session.get(ProjectModel, "proj-own")
        await async_session.delete(owner)
        await async_session.flush()
        remaining = (
            await async_session.execute(select(ProjectRelationshipModel))
        ).scalars().all()
        assert list(remaining) == []

    async def test_set_null_on_neighbor_delete(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-from", "from")
        await _make_project(async_session, "proj-neighbor", "neighbor")
        repo = ProjectRelationshipRepository(async_session)
        edge = await repo.add_relationship(
            {
                "project_id": "proj-from",
                "relation_type": "parent",
                "location_kind": "gludd_project_name",
                "location_value": "neighbor",
                "related_project_id": "proj-neighbor",
            }
        )
        assert edge.related_project_id == "proj-neighbor"
        # Delete the NEIGHBOR -> edge survives with related_project_id SET NULL.
        neighbor = await async_session.get(ProjectModel, "proj-neighbor")
        await async_session.delete(neighbor)
        await async_session.flush()
        await async_session.refresh(edge)
        assert edge.related_project_id is None
        # The owning edge itself is NOT deleted (declared intent preserved).
        survivors = await repo.list_for_project("proj-from")
        assert len(survivors) == 1


class TestConfigParsing:
    def test_kind_inference(self):
        url = normalize_relationship_config(
            {"relation": "external", "location": "https://api.stripe.com"}
        )
        assert url is not None and url["location_kind"] == "url"
        directory = normalize_relationship_config(
            {"relation": "child", "location": "./services/ledger"}
        )
        assert directory is not None and directory["location_kind"] == "directory"
        name = normalize_relationship_config(
            {"relation": "parent", "location": "acme-platform"}
        )
        assert name is not None and name["location_kind"] == "gludd_project_name"

    def test_explicit_kind_wins_over_inference(self):
        edge = normalize_relationship_config(
            {"relation": "child", "location": "ledger", "kind": "directory"}
        )
        assert edge is not None
        assert edge["location_kind"] == "directory"

    def test_malformed_entries_skipped(self):
        assert normalize_relationship_config({"relation": "bogus", "location": "x"}) is None
        assert normalize_relationship_config({"relation": "parent", "location": ""}) is None
        assert normalize_relationship_config({"location": "x"}) is None

    def test_parse_relationships_block(self):
        pcfg = {
            "relationships": [
                {
                    "relation": "parent",
                    "location": "acme-platform",
                    "kind": "gludd_project_name",
                    "controlled_by_gludd": True,
                    "interface_hint": "GET /oauth/introspect",
                },
                {"relation": "child", "location": "./services/ledger"},
                {"relation": "garbage", "location": "x"},  # dropped
            ]
        }
        edges = parse_relationships(pcfg)
        assert len(edges) == 2
        assert edges[0]["relation_type"] == "parent"
        assert edges[0]["controlled_by_gludd"] is True
        assert edges[1]["location_kind"] == "directory"

    def test_seed_from_config_carries_relationships(self):
        cfg = {
            "projects": [
                {
                    "name": "payments-api",
                    "weight": 40,
                    "relationships": [
                        {"relation": "parent", "location": "acme-platform"},
                    ],
                }
            ]
        }
        mgr = seed_from_config(cfg)
        projects = mgr.list_projects()
        assert len(projects) == 1
        rels = projects[0].config.get("relationships")
        assert rels and rels[0]["relation_type"] == "parent"


class TestPersistRelationshipsFromConfig:
    async def test_maps_block_to_rows_with_resolution(
        self, async_session: AsyncSession
    ):
        # Two gludd projects so the parent edge resolves to a real neighbor.
        await _make_project(async_session, "proj-pay", "payments-api")
        await _make_project(async_session, "proj-plat", "acme-platform")
        rel_repo = ProjectRelationshipRepository(async_session)
        edges = parse_relationships(
            {
                "relationships": [
                    {"relation": "parent", "location": "acme-platform"},
                    {"relation": "external", "location": "https://api.stripe.com"},
                ]
            }
        )
        rows = await persist_relationships_from_config(
            rel_repo,
            "proj-pay",
            edges,
            name_to_id={"acme-platform": "proj-plat", "payments-api": "proj-pay"},
        )
        assert len(rows) == 2
        parent = await rel_repo.get_parent("proj-pay")
        assert parent is not None
        # Resolved gludd neighbor -> related_project_id set, controlled defaults True.
        assert parent.related_project_id == "proj-plat"
        assert parent.controlled_by_gludd is True
        # External URL is unresolved -> related_project_id stays NULL.
        external = await rel_repo.list_for_project(
            "proj-pay", relation_type="external"
        )
        assert external[0].related_project_id is None

    async def test_unresolved_neighbor_kept_with_null(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-pay", "payments-api")
        rel_repo = ProjectRelationshipRepository(async_session)
        edges = parse_relationships(
            {"relationships": [{"relation": "parent", "location": "ghost-platform"}]}
        )
        rows = await persist_relationships_from_config(
            rel_repo, "proj-pay", edges, name_to_id={}
        )
        assert len(rows) == 1
        assert rows[0].related_project_id is None
        # Declared intent preserved.
        assert rows[0].location_value == "ghost-platform"

    async def test_reseed_is_idempotent(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-pay", "payments-api")
        rel_repo = ProjectRelationshipRepository(async_session)
        edges = parse_relationships(
            {"relationships": [{"relation": "child", "location": "./svc"}]}
        )
        await persist_relationships_from_config(rel_repo, "proj-pay", edges)
        await persist_relationships_from_config(rel_repo, "proj-pay", edges)
        all_edges = await rel_repo.list_for_project("proj-pay")
        assert len(all_edges) == 1

    async def test_explicit_uncontrolled_not_overridden(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-pay", "payments-api")
        await _make_project(async_session, "proj-plat", "acme-platform")
        rel_repo = ProjectRelationshipRepository(async_session)
        edges = parse_relationships(
            {
                "relationships": [
                    {
                        "relation": "parent",
                        "location": "acme-platform",
                        "controlled_by_gludd": False,
                    }
                ]
            }
        )
        rows = await persist_relationships_from_config(
            rel_repo,
            "proj-pay",
            edges,
            name_to_id={"acme-platform": "proj-plat"},
        )
        # Resolved, but operator explicitly said uncontrolled -> stays False.
        assert rows[0].related_project_id == "proj-plat"
        assert rows[0].controlled_by_gludd is False
