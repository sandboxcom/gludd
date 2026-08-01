"""Add durable, fenced Azure billed-cost reconciliation ledger.

Revision ID: 038
Revises: 037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_PAYLOAD_LEN = 65536


def upgrade() -> None:
    op.create_table(
        "azure_cost_predictions",
        sa.Column("prediction_id", sa.String(length=64), nullable=False),
        sa.Column("prediction_version", sa.Integer(), nullable=False),
        sa.Column("todo_id", sa.String(length=64), nullable=False),
        sa.Column("identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("identity_payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("state_rank", sa.Integer(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "prediction_version > 0",
            name="ck_azure_cost_predictions_version_positive",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name="ck_azure_cost_predictions_fencing_nonnegative",
        ),
        sa.CheckConstraint(
            "state_rank >= 0 AND state_rank <= 7",
            name="ck_azure_cost_predictions_state_rank",
        ),
        sa.CheckConstraint(
            "((lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))",
            name="ck_azure_cost_predictions_lease_pair",
        ),
        sa.CheckConstraint(
            f"length(identity_payload) <= {_MAX_PAYLOAD_LEN}",
            name="ck_azure_cost_predictions_identity_payload_len",
        ),
        sa.PrimaryKeyConstraint(
            "prediction_id",
            "prediction_version",
            name="pk_azure_cost_predictions",
        ),
    )
    op.create_index(
        "ix_azure_cost_predictions_todo_id",
        "azure_cost_predictions",
        ["todo_id"],
    )
    op.create_index(
        "ix_azure_cost_predictions_state",
        "azure_cost_predictions",
        ["state"],
    )
    op.create_index(
        "ix_azure_cost_predictions_not_before",
        "azure_cost_predictions",
        ["not_before"],
    )
    op.create_index(
        "ix_azure_cost_predictions_lease_expires_at",
        "azure_cost_predictions",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_azure_cost_predictions_due_claim",
        "azure_cost_predictions",
        ["state_rank", "not_before", "lease_expires_at"],
    )

    op.create_table(
        "azure_cost_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("prediction_id", sa.String(length=64), nullable=False),
        sa.Column("prediction_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=256), nullable=False),
        sa.Column("row_identity", sa.String(length=256), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fencing_token > 0",
            name="ck_azure_cost_observations_fencing_positive",
        ),
        sa.CheckConstraint(
            f"length(payload) <= {_MAX_PAYLOAD_LEN}",
            name="ck_azure_cost_observations_payload_len",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id", "prediction_version"],
            [
                "azure_cost_predictions.prediction_id",
                "azure_cost_predictions.prediction_version",
            ],
            name="fk_azure_cost_observations_prediction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_azure_cost_observations"),
        sa.UniqueConstraint(
            "prediction_id",
            "prediction_version",
            "source",
            "snapshot_id",
            "row_identity",
            name="uq_azure_cost_observation_identity",
        ),
    )
    op.create_index(
        "ix_azure_cost_observations_prediction",
        "azure_cost_observations",
        ["prediction_id", "prediction_version"],
    )

    op.create_table(
        "azure_cost_outbox_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_id", sa.String(length=64), nullable=False),
        sa.Column("prediction_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("deduplication_key", sa.String(length=256), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"length(payload) <= {_MAX_PAYLOAD_LEN}",
            name="ck_azure_cost_outbox_events_payload_len",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id", "prediction_version"],
            [
                "azure_cost_predictions.prediction_id",
                "azure_cost_predictions.prediction_version",
            ],
            name="fk_azure_cost_outbox_prediction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_azure_cost_outbox_events"),
        sa.UniqueConstraint(
            "deduplication_key",
            name="uq_azure_cost_outbox_deduplication_key",
        ),
    )
    op.create_index(
        "ix_azure_cost_outbox_events_event_type",
        "azure_cost_outbox_events",
        ["event_type"],
    )
    op.create_index(
        "ix_azure_cost_outbox_pending",
        "azure_cost_outbox_events",
        ["published_at", "created_at"],
    )
    op.create_index(
        "ix_azure_cost_outbox_prediction",
        "azure_cost_outbox_events",
        ["prediction_id", "prediction_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_azure_cost_outbox_prediction",
        table_name="azure_cost_outbox_events",
    )
    op.drop_index(
        "ix_azure_cost_outbox_pending",
        table_name="azure_cost_outbox_events",
    )
    op.drop_index(
        "ix_azure_cost_outbox_events_event_type",
        table_name="azure_cost_outbox_events",
    )
    op.drop_table("azure_cost_outbox_events")
    op.drop_index(
        "ix_azure_cost_observations_prediction",
        table_name="azure_cost_observations",
    )
    op.drop_table("azure_cost_observations")
    op.drop_index(
        "ix_azure_cost_predictions_due_claim",
        table_name="azure_cost_predictions",
    )
    op.drop_index(
        "ix_azure_cost_predictions_lease_expires_at",
        table_name="azure_cost_predictions",
    )
    op.drop_index(
        "ix_azure_cost_predictions_not_before",
        table_name="azure_cost_predictions",
    )
    op.drop_index(
        "ix_azure_cost_predictions_state",
        table_name="azure_cost_predictions",
    )
    op.drop_index(
        "ix_azure_cost_predictions_todo_id",
        table_name="azure_cost_predictions",
    )
    op.drop_table("azure_cost_predictions")
