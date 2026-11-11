"""The one-off Outlook take-over (SPEC §5.4): a past_bookings history table, and on bookings the
iCalendar UID of the Outlook event a booking was taken over from (so a second run skips it).

Revision ID: 97e1ca6e189d
Revises: 4c1e7a2b9f30
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic's bookkeeping: this migration's id and the one it builds on.
revision: str = "97e1ca6e189d"
down_revision: str | Sequence[str] | None = "4c1e7a2b9f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this change."""
    op.create_table(
        "past_bookings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("people", sa.Integer(), nullable=True),
        sa.Column("ticket_type", sa.String(length=20), nullable=True),
        sa.Column("type_source", sa.String(length=20), nullable=True),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("was_soft_block", sa.Boolean(), nullable=False),
        sa.Column("source_event_id", sa.String(length=300), nullable=False),
        sa.Column("original_subject", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id", name="uq_past_bookings_source_event_id"),
    )
    op.create_index("ix_past_bookings_date", "past_bookings", ["date"])

    # batch_alter_table lets SQLite (which can't add constraints in place) do the same as PostgreSQL.
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("source_event_id", sa.String(length=300), nullable=True))
        batch.create_unique_constraint("uq_bookings_source_event_id", ["source_event_id"])


def downgrade() -> None:
    """Undo this change. The history table and its rows are lost."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_constraint("uq_bookings_source_event_id", type_="unique")
        batch.drop_column("source_event_id")
    op.drop_index("ix_past_bookings_date", table_name="past_bookings")
    op.drop_table("past_bookings")
