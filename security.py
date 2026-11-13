
import os
import secrets

from dotenv import load_dotenv
from fastapi import Header, HTTPException, Request

load_dotenv()

API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise RuntimeError("API_KEY is missing from environment")


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None)
):
    # The browser experience uses the public web proxy routes. Those routes
    # call the same protected booking logic server-side, so the API key never
    # needs to be exposed to patients.
    if request.url.path.startswith("/web/"):
        return

    if not x_api_key or not secrets.compare_digest(
        x_api_key, API_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )
