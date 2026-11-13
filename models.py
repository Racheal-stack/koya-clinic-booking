
from datetime import datetime
from uuid import uuid4
from sqlalchemy import String, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    provider: Mapped[str] = mapped_column(String)
    start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )
    slot_id: Mapped[str] = mapped_column(
        ForeignKey("slots.id")
    )
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(
        String, default="confirmed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now().astimezone()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    request_hash: Mapped[str] = mapped_column(String)
    status_code: Mapped[int] = mapped_column()
    response_body: Mapped[str] = mapped_column(String)
    response_headers: Mapped[str] = mapped_column(
    String,
    default="{}"
)

class BookingAttempt(Base):
    __tablename__ = "booking_attempts"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(
        String, index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
Index(
    "uq_confirmed_booking_per_slot",
    Booking.slot_id,
    unique=True,
    sqlite_where=(Booking.status == "confirmed"),
    postgresql_where=(Booking.status == "confirmed")
)