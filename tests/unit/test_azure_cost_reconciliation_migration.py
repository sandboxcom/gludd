"""Alembic contract for durable Azure billed-cost reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect


def _upgrade(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    config = AlembicConfig()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    command.upgrade(config, "head")


def test_migration_creates_reconciliation_tables_and_uniqueness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "azure-cost.db"
    _upgrade(db_path, monkeypatch)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        schema = inspect(engine)
        assert {
            "azure_cost_predictions",
            "azure_cost_observations",
            "azure_cost_outbox_events",
        }.issubset(schema.get_table_names())
        observation_uniques = {
            tuple(item["column_names"])
            for item in schema.get_unique_constraints("azure_cost_observations")
        }
        assert (
            "prediction_id",
            "prediction_version",
            "source",
            "snapshot_id",
            "row_identity",
        ) in observation_uniques
        outbox_uniques = {
            tuple(item["column_names"])
            for item in schema.get_unique_constraints("azure_cost_outbox_events")
        }
        assert ("deduplication_key",) in outbox_uniques
    finally:
        engine.dispose()


def test_migration_round_trips_its_three_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "azure-cost-roundtrip.db"
    _upgrade(db_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    config = AlembicConfig()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    command.downgrade(config, "037")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        schema = inspect(engine)
        assert "azure_cost_predictions" not in schema.get_table_names()
        assert "azure_cost_observations" not in schema.get_table_names()
        assert "azure_cost_outbox_events" not in schema.get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        assert "azure_cost_predictions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
