

import base64
import binascii
import hashlib
import json
import math
import re
from fastapi.staticfiles import StaticFiles

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from pydantic import BaseModel, field_validator, ValidationError

from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import (
    Slot,
    Booking,
    IdempotencyRecord,
    BookingAttempt
)
from security import verify_api_key



# --------------------------------------------------
# Application setup
# --------------------------------------------------

app = FastAPI(
    title="Koya Clinic Booking API",
    description="Clinic appointment booking API",
    version="1.0.0",
    dependencies=[Depends(verify_api_key)]
)

Base.metadata.create_all(bind=engine)


# --------------------------------------------------
# Error handlers
# --------------------------------------------------

@app.exception_handler(HTTPException)
async def handle_http_error(
    request: Request,
    exc: HTTPException
):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail)},
        headers=exc.headers
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request,
    exc: RequestValidationError
):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid request data"}
    )


# --------------------------------------------------
# Request models
# --------------------------------------------------

class SlotInput(BaseModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class ResetInput(BaseModel):
    slots: list[SlotInput]


class BookingInput(BaseModel):
    slot_id: str
    name: str
    email: str

    @field_validator("slot_id", "name", "email")
    @classmethod
    def validate_required(cls, value):
        value = value.strip()

        if not value:
            raise ValueError("Field cannot be empty")

        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value
        ):
            raise ValueError("Invalid email address")

        return value.lower()


class CancellationInput(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value
        ):
            raise ValueError("Invalid email address")
        return value


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def booking_response(booking):
    return {
        "id": booking.id,
        "slot_id": booking.slot_id,
        "name": booking.name,
        "email": booking.email,
        "status": booking.status,
        "created_at": booking.created_at.isoformat()
    }


def to_utc(value: datetime):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


# --------------------------------------------------
# 1. POST /reset
# Replace all slots and remove existing bookings
# --------------------------------------------------

@app.post("/reset")
def reset(
    data: ResetInput,
    db: Session = Depends(get_db)
):
    try:
        # /reset is used to create a clean assessment/test fixture. Clear
        # related request state as well as slots and bookings so an old
        # idempotency key cannot affect a fresh reset.
        db.execute(delete(IdempotencyRecord))
        db.execute(delete(BookingAttempt))
        db.execute(delete(Booking))
        db.execute(delete(Slot))

        for item in data.slots:
            slot = Slot(
                id=item.id,
                provider=item.provider,
                start=to_utc(item.start).replace(tzinfo=None),
                end=to_utc(item.end).replace(tzinfo=None)
            )

            db.add(slot)

        db.commit()

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Invalid slot data"
        )

    return {
        "message": "Slots reset successfully"
    }


# --------------------------------------------------
# 2. GET /slots
# List all slots with optional provider filter
# --------------------------------------------------

@app.get("/slots")
def list_slots(
    provider: str | None = None,
    db: Session = Depends(get_db)
):
    query = select(Slot).order_by(
        Slot.start,
        Slot.id
    )

    if provider is not None:
        query = query.where(
            Slot.provider == provider
        )

    slots = db.scalars(query).all()

    result = []

    for slot in slots:
        existing = db.scalar(
            select(Booking.id).where(
                Booking.slot_id == slot.id,
                Booking.status == "confirmed"
            )
        )

        result.append({
            "id": slot.id,
            "provider": slot.provider,
            "start": to_utc(slot.start).isoformat(),
            "end": to_utc(slot.end).isoformat(),
            "available": existing is None
        })

    return {"slots": result}


# --------------------------------------------------
# 3. POST /bookings
# Create a confirmed booking
# --------------------------------------------------

@app.post("/bookings", status_code=201)
async def create_booking(
    request: Request,
    db: Session = Depends(get_db)
):
    # Read the original request body
    try:
        raw_data = await request.json()
        if not isinstance(raw_data, dict):
            raise ValueError("Expected a JSON object")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid request data"
        )

    key = request.headers.get("Idempotency-Key")

    if key is not None and not key.strip():
        raise HTTPException(
            status_code=400,
            detail="Invalid idempotency key"
        )

    # Canonical fingerprint of the request body
    canonical_body = json.dumps(
        raw_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )
    request_hash = hashlib.sha256(
        canonical_body.encode("utf-8")
    ).hexdigest()

    def response(status, body, retry_after=None):
        headers = {}

        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)

        return JSONResponse(
            status_code=status,
            content=body,
            headers=headers
        )

    # SQLite: acquire a write lock before checking
    # availability, rate limits, or idempotency.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    # Idempotency lookup
    if key is not None:
        previous = db.get(IdempotencyRecord, key)

        if previous is not None:
            if previous.request_hash != request_hash:
                db.rollback()
                return response(
                    422,
                    {"error": "Idempotency key reused with different body"}
                )

            
            result = json.loads(previous.response_body)
            headers = json.loads(
                previous.response_headers or "{}"
            )

            db.rollback()

            return JSONResponse(
                status_code=previous.status_code,
                content=result,
                headers=headers
            )


    def finish(status, body, retry_after=None):
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        if key is not None:
            record = IdempotencyRecord(
                key=key,
                request_hash=request_hash,
                status_code=status,
                response_body=json.dumps(body),
                response_headers=json.dumps(headers)
            )
            db.add(record)

        # Commit both ordinary bookings and idempotent bookings. Without an
        # idempotency key, the previous implementation returned 201/null and
        # discarded the booking when the session closed.
        db.commit()

        return JSONResponse(
            status_code=status,
            content=body,
            headers=headers
        )

    # Extract email before full validation so malformed
    # booking attempts can still count toward the limit.
    email_value = raw_data.get("email")
    email = (
        email_value.strip().lower()
        if isinstance(email_value, str)
        else None
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=60)

    if email:
        # Remove expired attempt records
        db.execute(
            delete(BookingAttempt).where(
                BookingAttempt.attempted_at <
                cutoff.replace(tzinfo=None)
            )
        )

        recent_attempts = db.scalars(
            select(BookingAttempt)
            .where(BookingAttempt.email == email)
            .order_by(BookingAttempt.attempted_at)
        ).all()

        # Record every attempt, including failed ones
        db.add(BookingAttempt(
            id=str(uuid4()),
            email=email,
            attempted_at=now.replace(tzinfo=None)
        ))

        if len(recent_attempts) >= 5:
            oldest = recent_attempts[0].attempted_at
            oldest = to_utc(oldest)

            retry_after = max(
                1,
                math.ceil(
                    60 - (now - oldest).total_seconds()
                )
            )

            return finish(
                429,
                {"error": "Rate limit exceeded"},
                retry_after
            )

    # Validate booking fields
    try:
        data = BookingInput.model_validate(raw_data)
    except ValidationError:
        return finish(
            400,
            {"error": "Invalid request data"}
        )

    slot = db.get(Slot, data.slot_id)

    if slot is None:
        return finish(
            404,
            {"error": "slot not found"}
        )

    if to_utc(slot.start) <= now:
        return finish(
            400,
            {"error": "slot has already started"}
        )

    existing = db.scalar(
        select(Booking.id).where(
            Booking.slot_id == data.slot_id,
            Booking.status == "confirmed"
        )
    )

    if existing is not None:
        return finish(
            409,
            {"error": "slot already booked"}
        )

    booking = Booking(
        id=str(uuid4()),
        slot_id=data.slot_id,
        name=data.name,
        email=data.email,
        status="confirmed",
        created_at=now.replace(tzinfo=None)
    )

    db.add(booking)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return response(
            409,
            {"error": "slot already booked"}
        )

    return finish(
        201,
        booking_response(booking)
    )


# --------------------------------------------------
# 4. GET /bookings
# List bookings by email with cursor pagination
# --------------------------------------------------

@app.get("/bookings")
def list_bookings(
    email: str,
    limit: int = 10,
    cursor: str | None = None,
    db: Session = Depends(get_db)
):
    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 50"
        )

    normalized_email = email.strip().lower()

    if not normalized_email:
        raise HTTPException(
            status_code=400,
            detail="email is required"
        )

    query = (
        select(Booking)
        .where(
            func.lower(Booking.email) == normalized_email
        )
        .order_by(
            Booking.created_at,
            Booking.id
        )
    )

    bookings = db.scalars(query).all()

    if cursor is not None:
        try:
            decoded_cursor = base64.b64decode(
                cursor.encode("ascii"),
                altchars=b"-_",
                validate=True
            ).decode("utf-8")

            cursor_index = next(
                index
                for index, booking in enumerate(bookings)
                if booking.id == decoded_cursor
            )

            bookings = bookings[cursor_index + 1:]

        except (
            ValueError,
            StopIteration,
            UnicodeDecodeError,
            UnicodeEncodeError,
            binascii.Error
        ):
            raise HTTPException(
                status_code=400,
                detail="invalid cursor"
            )

    page = bookings[:limit]

    next_cursor = None

    if len(bookings) > limit and page:
        next_cursor = base64.urlsafe_b64encode(
            page[-1].id.encode("utf-8")
        ).decode("ascii")

    return {
        "bookings": [
            booking_response(booking)
            for booking in page
        ],
        "next_cursor": next_cursor
    }


# --------------------------------------------------
# 5. GET /bookings/{booking_id}
# Retrieve a booking
# --------------------------------------------------

@app.get("/bookings/{booking_id}")
def get_booking(
    booking_id: str,
    db: Session = Depends(get_db)
):
    booking = db.get(Booking, booking_id)

    if booking is None:
        raise HTTPException(
            status_code=404,
            detail="booking not found"
        )

    return booking_response(booking)


# --------------------------------------------------
# 6. DELETE /bookings/{booking_id}
# Cancel a booking and free its slot
# --------------------------------------------------

@app.delete("/bookings/{booking_id}")
def cancel_booking(
    booking_id: str,
    db: Session = Depends(get_db)
):
    booking = db.get(Booking, booking_id)

    if booking is None:
        raise HTTPException(
            status_code=404,
            detail="booking not found"
        )

    if booking.status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="booking already cancelled"
        )

    booking.status = "cancelled"

    db.commit()
    db.refresh(booking)

    return booking_response(booking)


# --------------------------------------------------
# Public patient web proxy routes
# The browser uses these same-origin routes. They intentionally do not expose
# the API key; the protected API routes above remain available for evaluators
# and integrations that send X-API-Key.
# --------------------------------------------------

@app.get("/web/slots", include_in_schema=False)
def web_list_slots(
    provider: str | None = None,
    db: Session = Depends(get_db)
):
    return list_slots(provider=provider, db=db)


@app.post("/web/bookings", status_code=201, include_in_schema=False)
async def web_create_booking(
    request: Request,
    db: Session = Depends(get_db)
):
    return await create_booking(request=request, db=db)


@app.post("/web/bookings/{booking_id}/cancel", include_in_schema=False)
def web_cancel_booking(
    booking_id: str,
    data: CancellationInput,
    db: Session = Depends(get_db)
):
    booking = db.get(Booking, booking_id)

    # Require both booking ID and matching email for the public patient flow.
    # Return a generic 404 so the route does not reveal whether an ID exists.
    if booking is None or booking.email.lower() != data.email:
        raise HTTPException(
            status_code=404,
            detail="booking not found"
        )

    return cancel_booking(booking_id=booking_id, db=db)

app.mount(
    "/",
    StaticFiles(directory="static", html=True),
    name="website"
)
