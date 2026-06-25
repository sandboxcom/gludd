"""Add project_relationships table (declared project topology edges).

Brings the migration chain into parity with the ``ProjectRelationshipModel`` ORM
model in ``general_ludd.db.models``. The table records USER-DECLARED edges from
one project to a neighbor (parent/child/sibling/external), identified by
``(location_kind, location_value)`` — a gludd project NAME, a DIRECTORY path, or
a URL. When the neighbor is itself a gludd project, ``related_project_id`` is
FK-linked; for external/unresolved neighbors it stays NULL.

Mirrors ``002`` (table + FK + index, with a matching ``downgrade``) and ``007``
(additive, dialect-guarded index). Two FKs to ``projects`` with different
``ondelete``: ``project_id`` is CASCADE (owning side), ``related_project_id`` is
SET NULL (resolved neighbor — losing it degrades the edge to "declared but
unresolved" rather than destroying the operator's declaration).

The "one parent per project" rule is not portably expressible as a SQL UNIQUE,
so on SQLite it is enforced by the repository guard
(``ProjectRelationshipRepository.set_parent``). PostgreSQL additionally gets a
partial unique index ``uq_one_parent``.

Revision ID: 008
Revises: 007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres(bind: sa.engine.Connection) -> bool:
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "project_relationships",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("relation_type", sa.String(16), nullable=False),
        sa.Column("location_kind", sa.String(32), nullable=False),
        sa.Column("location_value", sa.String(1024), nullable=False),
        sa.Column("related_project_id", sa.String(32), nullable=True),
        sa.Column(
            "controlled_by_gludd",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("interface_hint", sa.String(1024), nullable=True),
        sa.Column(
            "interface_contract", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.project_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_project_id"], ["projects.project_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "project_id",
            "relation_type",
            "location_kind",
            "location_value",
            name="uq_project_relationship_edge",
        ),
    )
    op.create_index(
        "ix_project_relationships_project_id",
        "project_relationships",
        ["project_id"],
    )
    op.create_index(
        "ix_project_relationships_relation_type",
        "project_relationships",
        ["relation_type"],
    )
    op.create_index(
        "ix_project_relationships_related_project_id",
        "project_relationships",
        ["related_project_id"],
    )
    op.create_index(
        "ix_project_rel_from_type",
        "project_relationships",
        ["project_id", "relation_type"],
    )
    op.create_index(
        "ix_project_rel_related",
        "project_relationships",
        ["related_project_id"],
    )
    # Postgres-only "one parent" partial unique index; SQLite relies on the repo
    # guard (ProjectRelationshipRepository.set_parent).
    if _is_postgres(op.get_bind()):
        op.execute(
            "CREATE UNIQUE INDEX uq_one_parent ON project_relationships(project_id) "
            "WHERE relation_type = 'parent'"
        )


def downgrade() -> None:
    if _is_postgres(op.get_bind()):
        op.execute("DROP INDEX IF EXISTS uq_one_parent")
    op.drop_index("ix_project_rel_related", "project_relationships")
    op.drop_index("ix_project_rel_from_type", "project_relationships")
    op.drop_index(
        "ix_project_relationships_related_project_id", "project_relationships"
    )
    op.drop_index(
        "ix_project_relationships_relation_type", "project_relationships"
    )
    op.drop_index(
        "ix_project_relationships_project_id", "project_relationships"
    )
    op.drop_table("project_relationships")
