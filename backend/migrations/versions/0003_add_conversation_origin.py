"""Record the accepted operation a conversation branched from."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("forked_from_operation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_forked_from_operation",
        "conversations",
        "operations",
        ["forked_from_operation_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conversations_forked_from_operation", "conversations", type_="foreignkey"
    )
    op.drop_column("conversations", "forked_from_operation_id")
