"""Backups and restore (SPEC §5.6): the state of the hourly backups, and Outlook sync paused after
a restore until the owner has checked the after-restore list.

Revision ID: 5e2a9c41b7d0
Revises: cd1148c97c27
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic's bookkeeping: this migration's id and the one it builds on.
revision: str = "5e2a9c41b7d0"
down_revision: str | Sequence[str] | None = "cd1148c97c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this change."""
    op.create_table(
        "backup_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_ok", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_file", sa.String(length=300), nullable=False),
        sa.Column("last_error", sa.String(), nullable=False),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # server_default fills the new columns of the existing row; then they are plain columns again.
    with op.batch_alter_table("outlook_state") as batch:
        batch.add_column(
            sa.Column("sync_paused", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("restored_from", sa.String(length=300), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("outlook_state") as batch:
        batch.alter_column("sync_paused", server_default=None)
        batch.alter_column("restored_from", server_default=None)


def downgrade() -> None:
    """Undo this change."""
    with op.batch_alter_table("outlook_state") as batch:
        batch.drop_column("restored_at")
        batch.drop_column("restored_from")
        batch.drop_column("sync_paused")
    op.drop_table("backup_state")
