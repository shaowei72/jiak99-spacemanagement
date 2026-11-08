"""Write audit log entries: every booking change, with who made it (SPEC §8)."""

from typing import Any

from sqlalchemy.orm import Session

from jiak_api import models


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Add one audit entry. It is saved with the rest of the transaction."""
    db.add(
        models.AuditLog(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )


def booking_snapshot(booking: models.Booking) -> dict[str, Any]:
    """The parts of a booking worth keeping in the audit log."""
    return {
        "date": booking.date,
        "start": booking.start,
        "ticketType": booking.ticket_type,
        "adults": booking.adults,
        "children": booking.children,
        "status": booking.status,
        "customerId": booking.customer_id,
        "customer": {
            "name": booking.customer.name,
            "phone": booking.customer.phone,
            "email": booking.customer.email,
            "company": booking.customer.company,
        },
        "notes": booking.notes,
        "sessions": [
            {"id": s.id, "adults": s.adults, "children": s.children, "zoneId": s.zone_id}
            for s in booking.sessions
        ],
    }
