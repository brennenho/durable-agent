"""Group existing and new runs into conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("runs", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("turn_index", sa.Integer(), server_default="0", nullable=False))
    op.execute("INSERT INTO conversations (id, created_at) SELECT id, created_at FROM runs")
    op.execute("UPDATE runs SET conversation_id = id")
    op.alter_column("runs", "conversation_id", nullable=False)
    op.create_foreign_key(
        "runs_conversation_id_fkey", "runs", "conversations", ["conversation_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_runs_conversation_turn", "runs", ["conversation_id", "turn_index"]
    )
    op.create_check_constraint("ck_runs_turn_index", "runs", "turn_index >= 0")
    op.create_index(
        "uq_runs_one_active_per_conversation",
        "runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'retrying')"),
    )


def downgrade() -> None:
    op.drop_index("uq_runs_one_active_per_conversation", table_name="runs")
    op.drop_constraint("ck_runs_turn_index", "runs", type_="check")
    op.drop_constraint("uq_runs_conversation_turn", "runs", type_="unique")
    op.drop_constraint("runs_conversation_id_fkey", "runs", type_="foreignkey")
    op.drop_column("runs", "turn_index")
    op.drop_column("runs", "conversation_id")
    op.drop_table("conversations")
