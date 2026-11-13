
import os
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://127.0.0.1:8000"
API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise RuntimeError("API_KEY is missing")


def call(method, path, data=None, key=None):
    headers = {"X-API-Key": API_KEY}

    if data is not None:
        headers["Content-Type"] = "application/json"

    if key is not None:
        headers["Idempotency-Key"] = key

    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as res:
            return (
                res.status,
                json.loads(res.read()),
                dict(res.headers)
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read()),
            dict(exc.headers)
        )


def assert_status(actual, expected, label):
    if actual != expected:
        raise AssertionError(
            f"{label}: expected {expected}, got {actual}"
        )


run_id = uuid.uuid4().hex[:10]
future = datetime.now(timezone.utc) + timedelta(days=30)

# Add test slots. WARNING: /reset deletes existing
# local bookings and slots.
slots = []

for i in range(3):
    start = future + timedelta(hours=i)
    end = start + timedelta(minutes=30)

    slots.append({
        "id": f"{run_id}-slot-{i}",
        "provider": "Dr Test",
        "start": start.isoformat(),
        "end": end.isoformat()
    })

status, body, _ = call(
    "POST",
    "/reset",
    {"slots": slots}
)

assert_status(status, 200, "Reset")
print("RESET: PASS")


# -----------------------------------------------
# TEST 1: Cancellation and rebooking
# -----------------------------------------------

first_body = {
    "slot_id": slots[0]["id"],
    "name": "First Patient",
    "email": f"first-{run_id}@example.com"
}

status, booking, _ = call(
    "POST", "/bookings", first_body
)

print("BOOKING STATUS:", status)
print("BOOKING RESPONSE:", booking)

assert_status(status, 201, "Initial booking")

assert isinstance(booking, dict), (
    f"Expected booking object, got: {booking!r}"
)

assert "id" in booking, (
    f"Booking response is missing id: {booking}"
)

booking_id = booking["id"]

status, cancelled, _ = call(
    "DELETE", f"/bookings/{booking_id}"
)
assert_status(status, 200, "Cancellation")

assert cancelled["status"] == "cancelled"

second_body = {
    "slot_id": slots[0]["id"],
    "name": "Second Patient",
    "email": f"second-{run_id}@example.com"
}

status, new_booking, _ = call(
    "POST", "/bookings", second_body
)

assert_status(status, 201, "Rebooking")
assert new_booking["id"] != booking_id

print("CANCELLATION + REBOOKING: PASS")


# -----------------------------------------------
# TEST 2: Idempotency key conflict
# -----------------------------------------------

key = str(uuid.uuid4())

body_a = {
    "slot_id": slots[1]["id"],
    "name": "Ada",
    "email": f"ada-{run_id}@example.com"
}

body_b = {
    "slot_id": slots[1]["id"],
    "name": "Grace",
    "email": f"ada-{run_id}@example.com"
}

status, _, _ = call(
    "POST", "/bookings", body_a, key
)
assert_status(status, 201, "First idempotent request")

status, response, _ = call(
    "POST", "/bookings", body_b, key
)
assert_status(status, 422, "Idempotency conflict")

print("IDEMPOTENCY CONFLICT: PASS")


# -----------------------------------------------
# TEST 3: Retry-After header
# -----------------------------------------------

limited_email = f"limited-{run_id}@example.com"

statuses = []

for i in range(6):
    status, body, headers = call(
        "POST",
        "/bookings",
        {
            "slot_id": "missing-slot",
            "name": "Rate Test",
            "email": limited_email
        }
    )
    statuses.append(status)

assert statuses == [404] * 5 + [429], statuses


retry_after = next(
    (
        value
        for name, value in headers.items()
        if name.lower() == "retry-after"
    ),
    None
)

print("429 RESPONSE HEADERS:", headers)
print("RETRY-AFTER:", retry_after)

assert retry_after is not None, (
    "429 response is missing Retry-After"
)

assert retry_after.isdigit(), (
    "Retry-After must be an integer"
)

assert int(retry_after) > 0

print("RATE LIMIT RETRY-AFTER: PASS")
print("Retry-After:", retry_after)


# -----------------------------------------------
# TEST 4: Independent email limits
# -----------------------------------------------

other_email = f"other-{run_id}@example.com"

status, _, _ = call(
    "POST",
    "/bookings",
    {
        "slot_id": "missing-slot",
        "name": "Another Patient",
        "email": other_email
    }
)

assert_status(status, 404, "Independent email")

print("INDEPENDENT EMAIL LIMITS: PASS")
print("ALL EDGE-CASE TESTS PASSED")
