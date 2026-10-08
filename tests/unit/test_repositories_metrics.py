"""Dialect-selection tests for metrics repositories."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.repositories.metrics import PromptProfileRepository


def test_prompt_profile_repository_retains_the_caller_session() -> None:
    """Dialect dispatch must not replace or take ownership of the session."""
    session = MagicMock(spec=AsyncSession)
    typed_session = cast("AsyncSession", session)

    assert PromptProfileRepository(typed_session)._session is typed_session


@pytest.mark.asyncio
async def test_prompt_profile_upsert_fails_before_io_for_unknown_dialect() -> None:
    """Prompt-profile writes fail closed rather than executing mismatched SQL."""
    session = MagicMock(spec=AsyncSession)
    session.get_bind.return_value.dialect.name = "mysql"
    typed_session = cast("AsyncSession", session)

    with pytest.raises(ValueError, match="Repository upserts do not support SQL dialect 'mysql'"):
        await PromptProfileRepository(typed_session).upsert(
            {
                "name": "dialect-upsert",
                "source": "test",
                "prompt_text": "Use the bound database dialect.",
            }
        )

    session.execute.assert_not_called()
