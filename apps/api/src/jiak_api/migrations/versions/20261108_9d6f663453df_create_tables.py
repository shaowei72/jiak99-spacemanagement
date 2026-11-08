"""Create the first tables: customers, bookings, sessions, settings and the audit log.

Revision ID: 9d6f663453df
Revises:
Created: 2026-11-08 10:25:05.095447
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic's bookkeeping: this migration's id and the one it builds on.
revision: str = "9d6f663453df"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this change."""
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("turnaround_minutes", sa.Integer(), nullable=False),
        sa.Column("small_group_warn_below", sa.Integer(), nullable=False),
        sa.Column("alternative_day_search", sa.Integer(), nullable=False),
        sa.Column("availability_calendar_days", sa.Integer(), nullable=False),
        sa.Column("prices", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "day_settings",
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("blackouts", sa.JSON(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("ticket_type", sa.String(length=20), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("outlook_event_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookings_date", "bookings", ["date"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("booking_id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("ticket_type", sa.String(length=20), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.String(length=1), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_date", "sessions", ["date"])


def downgrade() -> None:
    """Undo this change."""
    op.drop_index("ix_sessions_date", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_bookings_date", table_name="bookings")
    op.drop_table("bookings")
    op.drop_table("day_settings")
    op.drop_table("customers")
    op.drop_table("audit_log")
    op.drop_table("app_settings")
