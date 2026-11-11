"""Outlook → App (SPEC §5.4): what each booking's event showed when the app last wrote it, a note
for the event, the Outlook link's state, dates still to write, and hand-typed entries already marked.

Existing bookings start with no stored event state, so the next sync writes their events afresh.

Revision ID: cd1148c97c27
Revises: 97e1ca6e189d
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic's bookkeeping: this migration's id and the one it builds on.
revision: str = "cd1148c97c27"
down_revision: str | Sequence[str] | None = "97e1ca6e189d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this change."""
    op.create_table(
        "outlook_flags",
        sa.Column("event_id", sa.String(length=300), nullable=False),
        sa.Column("seen", sa.String(length=600), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "outlook_pending",
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("date"),
    )
    op.create_table(
        "outlook_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.String(length=300), nullable=True),
        sa.Column("last_write_ok", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_read_ok", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=False),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # batch_alter_table lets SQLite (which can't alter columns in place) do the same as PostgreSQL.
    # server_default fills the new note column of existing bookings with ''.
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("outlook_seen", sa.String(length=600), nullable=True))
        batch.add_column(sa.Column("outlook_note", sa.String(), nullable=False, server_default=""))
    with op.batch_alter_table("bookings") as batch:
        batch.alter_column("outlook_note", server_default=None)


def downgrade() -> None:
    """Undo this change."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("outlook_note")
        batch.drop_column("outlook_seen")
    op.drop_table("outlook_state")
    op.drop_table("outlook_pending")
    op.drop_table("outlook_flags")
