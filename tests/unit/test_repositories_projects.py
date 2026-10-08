"""Dialect-selection tests for project and feature repositories."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import FeatureStatus
from general_ludd.db.repositories.projects import FeatureRepository, VariableNamespaceRepository


def _unsupported_session() -> tuple[AsyncSession, MagicMock]:
    session = MagicMock(spec=AsyncSession)
    session.get_bind.return_value.dialect.name = "mysql"
    return cast("AsyncSession", session), session


def test_project_repositories_retain_the_caller_session() -> None:
    """Dialect dispatch must not replace or take ownership of the session."""
    session, _ = _unsupported_session()

    assert VariableNamespaceRepository(session)._session is session
    assert FeatureRepository(session)._session is session


@pytest.mark.asyncio
async def test_variable_upsert_fails_before_io_for_unknown_dialect() -> None:
    """Variable writes fail closed rather than executing mismatched SQL."""
    session, session_mock = _unsupported_session()

    with pytest.raises(ValueError, match="Repository upserts do not support SQL dialect 'mysql'"):
        await VariableNamespaceRepository(session).set_var("build", "workers", "2")

    session_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_feature_upsert_fails_before_io_for_unknown_dialect() -> None:
    """Feature writes fail closed rather than executing mismatched SQL."""
    session, session_mock = _unsupported_session()

    with pytest.raises(ValueError, match="Repository upserts do not support SQL dialect 'mysql'"):
        await FeatureRepository(session).upsert(
            {
                "name": "dialect-upsert",
                "description": "test",
                "category": "database",
                "status": FeatureStatus.REQUESTED,
            }
        )

    session_mock.execute.assert_not_called()
