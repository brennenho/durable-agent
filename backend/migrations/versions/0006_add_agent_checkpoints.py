"""Save SDK input, current state, output, and state at accepted operations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("agent_input", "agent_state", "output"):
        op.add_column("runs", sa.Column(name, JSONB(none_as_null=True), nullable=True))
    op.add_column("operations", sa.Column("agent_state", JSONB(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("operations", "agent_state")
    for name in ("output", "agent_state", "agent_input"):
        op.drop_column("runs", name)
