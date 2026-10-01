"""Create durable agent tables"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_key", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.Uuid(), nullable=True),
        sa.Column("lease_generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "terminal_error",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'failed') = (terminal_error IS NOT NULL)", name="ck_runs_terminal_error"
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_generation > 0) OR "
            "(status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_runs_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'retrying')) = (next_attempt_at IS NOT NULL)",
            name="ck_runs_claim_time",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_runs_finished",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed')",
            name="ck_runs_status",
        ),
        sa.CheckConstraint("lease_generation >= 0", name="ck_runs_generation"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_key", name="uq_runs_submission_key"),
    )
    op.create_index(
        "ix_runs_claimable",
        "runs",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'retrying')"),
    )
    op.create_index(
        "ix_runs_lease_expiry",
        "runs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "workspace_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("archive", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "request", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('model', 'tool')", name="ck_operations_kind"),
        sa.CheckConstraint(
            "(result IS NULL) = (completed_at IS NULL)", name="ck_operations_completion"
        ),
        sa.CheckConstraint("position >= 0", name="ck_operations_position"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "position", name="uq_operations_run_position"),
    )
    op.create_table(
        "tool_requests",
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column(
            "request", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("input_version_id", sa.Uuid(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("output_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(result IS NULL AND output_version_id IS NULL AND completed_at IS NULL) OR "
            "(result IS NOT NULL AND output_version_id IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_tool_requests_completion",
        ),
        sa.CheckConstraint("lease_generation > 0", name="ck_tool_requests_generation"),
        sa.ForeignKeyConstraint(
            ["input_version_id"],
            ["workspace_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["output_version_id"],
            ["workspace_versions.id"],
        ),
        sa.PrimaryKeyConstraint("operation_key"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "error", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column(
            "usage", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="ck_attempts_finished"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'accepted', 'error', 'unknown')", name="ck_attempts_status"
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_attempts_number"),
        sa.CheckConstraint("lease_generation > 0", name="ck_attempts_generation"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "attempt_no", name="uq_attempts_operation_number"),
    )
    op.create_index(
        "uq_attempts_one_active_or_accepted",
        "attempts",
        ["operation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'accepted')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_attempts_one_active_or_accepted",
        table_name="attempts",
        postgresql_where=sa.text("status IN ('running', 'accepted')"),
    )
    op.drop_table("attempts")
    op.drop_table("tool_requests")
    op.drop_table("operations")
    op.drop_table("workspace_versions")
    op.drop_index(
        "ix_runs_lease_expiry", table_name="runs", postgresql_where=sa.text("status = 'running'")
    )
    op.drop_index(
        "ix_runs_claimable",
        table_name="runs",
        postgresql_where=sa.text("status IN ('queued', 'retrying')"),
    )
    op.drop_table("runs")
