# Koya Clinic Booking API

FastAPI clinic appointment booking API with a small browser client.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_KEY='replace-with-a-long-random-key'
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/` to use the patient booking page.

The patient webpage uses the public same-origin `/web/slots` and
`/web/bookings` proxy routes, so patients do not enter or receive the API key.
The direct API routes such as `/slots` and `/bookings` remain protected by
`X-API-Key`.

## Environment variables

- `API_KEY` — required API key sent as `X-API-Key`.
- `DATABASE_URL` — SQLAlchemy database URL. For production use PostgreSQL, for example `postgresql+psycopg://...`.

The API supports listing and filtering slots, creating and retrieving bookings, cancellation, idempotency keys, cursor pagination, and per-email rate limiting.

## Deployment

Deploy the service with a persistent PostgreSQL database. Set `API_KEY` and `DATABASE_URL` in the host's environment settings, and use:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Do not use the default SQLite database for a multi-instance/serverless production deployment.
