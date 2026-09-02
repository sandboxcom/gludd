"""Contract and behavioral tests for the explicit database migration target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]


def test_migrate_up_target_is_parameterized_and_documented() -> None:
    """Pin the make-only migration surface and every required variable."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nmigrate-up:\n", 1)[1].split("\n\n", 1)[0]
    assert 'test -n "$(strip $(MIGRATE_DATABASE_URL))"' in target
    assert 'test -n "$(strip $(MIGRATE_REVISION))"' in target
    assert 'DATABASE_URL="$(MIGRATE_DATABASE_URL)"' in target
    assert 'alembic upgrade "$(MIGRATE_REVISION)"' in target

    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in contract["targets"] if item["name"] == "migrate-up")
    assert entry["make_variables"] == ["MIGRATE_DATABASE_URL", "MIGRATE_REVISION"]
    assert entry["behavior"] == (
        "make migrate-up "
        "MIGRATE_DATABASE_URL=sqlite:// "
        "MIGRATE_REVISION=head"
    )


def test_migrate_up_applies_the_complete_chain_to_an_isolated_database(
    tmp_path: Path,
) -> None:
    """Exercise the real target and verify revision 045's durable column."""
    database = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database}"

    result = subprocess.run(
        [
            "make",
            "migrate-up",
            f"MIGRATE_DATABASE_URL={database_url}",
            "MIGRATE_REVISION=head",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            columns = {
                column["name"] for column in sa.inspect(connection).get_columns("todos")
            }
    finally:
        engine.dispose()
    assert revision == "045"
    assert "approved_artifact_digest" in columns
