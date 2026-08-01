"""PostgreSQL engine and bootstrap-write parity tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.session import (
    init_engine_from_config,
    init_read_only_engine_from_config,
    seed_initial_queues,
)
from general_ludd.schemas.queue import INITIAL_QUEUES


class TestPostgresEngineInitialization:
    def test_explicit_postgres_url_builds_async_engine(self) -> None:
        engine = init_engine_from_config(
            {"url": "postgresql+psycopg://gludd:secret@db.example.com:5432/gludd"}
        )

        assert engine.dialect.name == "postgresql"
        assert str(engine.url).startswith("postgresql+psycopg://")

    def test_postgres_host_config_builds_async_engine(self) -> None:
        engine = init_engine_from_config(
            {
                "host": "db.example.com",
                "port": 5433,
                "name": "gludd",
                "user": "worker",
                "password": "secret",
            }
        )

        assert engine.dialect.name == "postgresql"
        assert engine.url.port == 5433

    def test_read_only_postgres_engine_is_supported(self) -> None:
        engine = init_read_only_engine_from_config(
            {"url": "postgresql+psycopg://reader:secret@db.example.com/gludd"}
        )

        assert engine.dialect.name == "postgresql"


@pytest.mark.asyncio
async def test_initial_queue_seed_uses_postgres_conflict_insert() -> None:
    """Bootstrap writes must compile for the session's actual SQL dialect."""
    session = MagicMock(spec=AsyncSession)
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind

    inserted = MagicMock(rowcount=1)
    listed = MagicMock()
    listed.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(
        side_effect=[*[inserted for _ in INITIAL_QUEUES], listed]
    )
    session.flush = AsyncMock()

    count = await seed_initial_queues(session)

    assert count == len(INITIAL_QUEUES)
    insert_calls = session.execute.await_args_list[: len(INITIAL_QUEUES)]
    assert insert_calls
    for call in insert_calls:
        statement = call.args[0]
        assert type(statement).__module__ == "sqlalchemy.dialects.postgresql.dml"
        assert statement._post_values_clause is not None

