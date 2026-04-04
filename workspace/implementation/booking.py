# workspace/implementation/booking.py
"""
Court booking API for Bay Club Connect.

Discovered endpoint (2026-04-03):
  POST https://connect-api.bayclubs.io/court-booking/api/1.0/courtbookings

Single-step booking — no temporary reservation step required.
"""
import asyncio
import logging
from typing import Optional

import aiohttp

from auth import LoginError
from availability import API_BASE, _api_headers

logger = logging.getLogger(__name__)


async def book_slot(
    token: str,
    club_id: str,
    slot: dict,
    category_options_id: str,
    time_slot_id: str,
    court_type_code: Optional[str],
) -> bool:
    """Book a specific court slot via the court-booking API.

    Args:
        token: Bearer token from login_and_get_token.
        club_id: Club UUID.
        slot: Dict with keys: date, court_id (UUID), from_minutes, to_minutes.
        category_options_id: From resolve_filter_ids.
        time_slot_id: From resolve_filter_ids.
        court_type_code: e.g. 'outdoor', or None.

    Returns:
        True on successful booking.

    Raises:
        LoginError: on HTTP 401.
        ConnectionError: on HTTP 5xx or network failure.
        RuntimeError: on HTTP 400 or other unexpected response.
    """
    date_str = slot["date"]
    body = {
        "clubId": club_id,
        "date": {"value": date_str, "date": date_str},
        "timeFromInMinutes": slot["from_minutes"],
        "timeToInMinutes": slot["to_minutes"],
        "courtId": slot["court_id"],
        "categoryOptionsId": category_options_id,
        "timeSlotId": time_slot_id,
    }
    if court_type_code:
        body["tennisCourtTypeCode"] = court_type_code

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/courtbookings",
                headers=_api_headers(token),
                json=body,
            ) as resp:
                if resp.status == 401:
                    raise LoginError("Auth token expired during booking (HTTP 401).")
                if resp.status >= 500:
                    raise ConnectionError(f"Booking API server error: HTTP {resp.status}")
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise RuntimeError(f"Booking failed (HTTP {resp.status}): {text[:300]}")
                logger.info(
                    "Booking confirmed: %s %s–%s court=%s",
                    date_str, slot.get("start_time", "?"), slot.get("end_time", "?"), slot.get("court", "?"),
                )
                return True
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ConnectionError(f"Network error during booking: {exc}") from exc
