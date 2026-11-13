
import os
import json
import uuid
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://127.0.0.1:8000"
API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise RuntimeError("API_KEY is missing from your environment")


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
            return res.status, json.loads(res.read())

    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# WARNING: This deletes existing test bookings.
status, result = call("POST", "/reset", {
    "slots": [
        {
            "id": "concurrency-test",
            "provider": "Dr Ade",
            "start": "2027-01-10T09:00:00Z",
            "end": "2027-01-10T09:30:00Z"
        },
        {
            "id": "idempotency-test",
            "provider": "Dr Ada",
            "start": "2027-01-10T10:00:00Z",
            "end": "2027-01-10T10:30:00Z"
        }
    ]
})

if status != 200:
    raise RuntimeError(f"Reset failed: {status} {result}")


# TEST 1: Concurrent bookings
def book_concurrently():
    return call("POST", "/bookings", {
        "slot_id": "concurrency-test",
        "name": "Concurrent Patient",
        "email": f"{uuid.uuid4()}@example.com"
    })


with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(
        lambda _: book_concurrently(),
        range(2)
    ))

concurrency_statuses = sorted(result[0] for result in results)
print("CONCURRENCY:", concurrency_statuses)


# TEST 2: Idempotency
body = {
    "slot_id": "idempotency-test",
    "name": "Ada Obi",
    "email": f"{uuid.uuid4()}@example.com"
}

key = str(uuid.uuid4())

first = call("POST", "/bookings", body, key)
second = call("POST", "/bookings", body, key)

print("IDEMPOTENCY:", first[0], second[0])
print("SAME RESPONSE:", first == second)


# TEST 3: Rate limiting
email = f"{uuid.uuid4()}@example.com"
statuses = []

for i in range(6):
    status, _ = call("POST", "/bookings", {
        "slot_id": "nonexistent-slot",
        "name": "Rate Test",
        "email": email
    })
    statuses.append(status)

print("RATE LIMIT:", statuses)
