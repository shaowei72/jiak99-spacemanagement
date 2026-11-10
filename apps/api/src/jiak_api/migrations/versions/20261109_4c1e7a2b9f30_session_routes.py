"""Sessions get a route (zone order, e.g. 'ABDC') instead of a single zone (SPEC v0.4).

A stored zone can't be turned into a route (a group now visits several zones), so every session
becomes floating again: route empty, not pinned. Locked days are unlocked too, since a locked
group with no route has nothing to keep. Ops allocates (and locks again), or the next booking
re-plans, to give them routes. Only example data existed when this was written (2026-11-09).

Revision ID: 4c1e7a2b9f30
Revises: 9d6f663453df
Created: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic's bookkeeping: this migration's id and the one it builds on.
revision: str = "4c1e7a2b9f30"
down_revision: str | Sequence[str] | None = "9d6f663453df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this change."""
    # batch_alter_table lets SQLite (which can't drop columns in place) do the same as PostgreSQL.
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("route", sa.String(length=4), nullable=True))
        batch.drop_column("zone_id")
    op.execute("UPDATE sessions SET pinned = false")
    op.execute("UPDATE sessions SET status = 'open' WHERE status = 'locked'")
    op.execute("UPDATE day_settings SET locked_at = NULL")


def downgrade() -> None:
    """Undo this change. Routes are lost; sessions come back without a zone."""
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("zone_id", sa.String(length=1), nullable=True))
        batch.drop_column("route")
