"""Give child runs a stable identity and let parents wait without a lease."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _statuses(waiting: bool) -> None:
    active = "'queued', 'running', 'retrying'" + (", 'waiting'" if waiting else "")
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status", "runs", f"status IN ({active}, 'succeeded', 'failed')"
    )
    op.drop_index("uq_runs_one_active_per_conversation", table_name="runs")
    op.create_index(
        "uq_runs_one_active_per_conversation",
        "runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({active})"),
    )


def upgrade() -> None:
    op.add_column("runs", sa.Column("parent_operation_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("child_index", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_runs_parent_operation", "runs", "operations", ["parent_operation_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_runs_parent_child", "runs", ["parent_operation_id", "child_index"]
    )
    op.create_check_constraint(
        "ck_runs_child_identity",
        "runs",
        "(parent_operation_id IS NULL AND child_index IS NULL) OR "
        "(parent_operation_id IS NOT NULL AND child_index IS NOT NULL "
        "AND child_index BETWEEN 0 AND 2)",
    )
    _statuses(waiting=True)


def downgrade() -> None:
    _statuses(waiting=False)
    op.drop_constraint("ck_runs_child_identity", "runs", type_="check")
    op.drop_constraint("uq_runs_parent_child", "runs", type_="unique")
    op.drop_constraint("fk_runs_parent_operation", "runs", type_="foreignkey")
    op.drop_column("runs", "child_index")
    op.drop_column("runs", "parent_operation_id")
