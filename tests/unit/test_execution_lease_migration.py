"""Alembic proof for fenced, renewable execution leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text

from general_ludd.db.migrations import get_alembic_config


def test_upgrade_deduplicates_legacy_buckets_and_adds_supervision_fields(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution-lease.db"
    url = f"sqlite:///{database}"
    config = get_alembic_config(url)
    command.upgrade(config, "045")
    now = datetime.now(UTC)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bucket_leases "
                    "(bucket_key, holder_id, expires_at, created_at) "
                    "VALUES (:bucket, :holder, :expires, :created)"
                ),
                [
                    {
                        "bucket": "core:TODO-LEASE",
                        "holder": "legacy-a",
                        "expires": (now - timedelta(minutes=2)).isoformat(),
                        "created": (now - timedelta(minutes=3)).isoformat(),
                    },
                    {
                        "bucket": "core:TODO-LEASE",
                        "holder": "legacy-b",
                        "expires": (now - timedelta(minutes=1)).isoformat(),
                        "created": (now - timedelta(minutes=2)).isoformat(),
                    },
                ],
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        schema = inspect(engine)
        columns = {column["name"]: column for column in schema.get_columns("bucket_leases")}
        assert {
            "todo_version",
            "heartbeat_at",
            "cancel_requested_at",
            "termination_confirmed_at",
            "updated_at",
        } <= set(columns)
        assert columns["heartbeat_at"]["nullable"] is False
        assert columns["updated_at"]["nullable"] is False
        unique = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in schema.get_unique_constraints("bucket_leases")
        }
        assert unique["uq_bucket_lease_bucket_key"] == ("bucket_key",)
        assert "uq_bucket_lease" not in unique
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT holder_id, heartbeat_at, updated_at "
                    "FROM bucket_leases WHERE bucket_key = :bucket"
                ),
                {"bucket": "core:TODO-LEASE"},
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["holder_id"] == "legacy-b"
        assert rows[0]["heartbeat_at"] is not None
        assert rows[0]["updated_at"] is not None
    finally:
        engine.dispose()
