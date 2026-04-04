# workspace/implementation/availability.py
import asyncio
import logging
from datetime import date, timedelta
from typing import List, Optional

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://connect-api.bayclubs.io/court-booking/api/1.0"
API_SUBSCRIPTION_KEY = "bac44a2d04b04413b6aea6d4e3aad294"


def date_range(from_date: date, to_date: date) -> list[date]:
    """Return dates from max(from_date, today) to min(to_date, today+3).

    Never returns dates in the past or more than 3 days ahead (API limit).
    """
    today = date.today()
    start = max(from_date, today)
    end = min(to_date, today + timedelta(days=3))
    if start > end:
        return []
    result = []
    d = start
    while d <= end:
        result.append(d)
        d += timedelta(days=1)
    return result


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": API_SUBSCRIPTION_KEY,
    }


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


async def resolve_club_id(token: str, location: str) -> tuple[str, str]:
    """Return (club_id, club_name) for the given location string."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/context", headers=_api_headers(token)) as resp:
            data = await resp.json()

    loc_lower = location.lower()
    for club in data.get("availableClubs", []):
        if (loc_lower in club.get("name", "").lower()
                or loc_lower in club.get("shortName", "").lower()):
            return club["id"], club["name"]

    available = [c["name"] for c in data.get("availableClubs", [])]
    raise ValueError(
        f"No club found matching '{location}'. Available: {', '.join(available)}"
    )


async def resolve_filter_ids(
    token: str,
    club_id: str,
    category_code: str,
    players: str,
    duration_minutes: int,
) -> tuple[str, str, Optional[str]]:
    """Return (categoryOptionsId, timeSlotId, tennisCourtTypeCode)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/filterContext",
            headers=_api_headers(token),
            params={"clubId": club_id},
        ) as resp:
            data = await resp.json()

    for cat in data.get("categories", []):
        if cat["category"]["code"].lower() != category_code.lower():
            continue
        court_types = cat.get("courtTypes", [])
        court_type_code = court_types[0]["code"] if court_types else None
        for opt in cat.get("options", []):
            if opt["name"].lower() != players.lower():
                continue
            for ts in opt.get("timeSlots", []):
                if ts["durationInMinutes"] == duration_minutes:
                    return opt["categoryOptionsId"], ts["id"], court_type_code
            available_durations = [ts["durationInMinutes"] for ts in opt.get("timeSlots", [])]
            raise ValueError(
                f"Duration {duration_minutes}min not available for {category_code}/{players}. "
                f"Available: {available_durations}"
            )

    available_sports = [c["category"]["code"] for c in data.get("categories", [])]
    raise ValueError(
        f"Sport '{category_code}' not available. Available: {', '.join(available_sports)}"
    )


async def fetch_available_slots(
    token: str,
    club_id: str,
    date_str: str,
    category_code: str,
    category_options_id: str,
    time_slot_id: str,
    court_type_code: Optional[str],
) -> List[dict]:
    """Call the availability API and return open slot dicts.

    Each slot: {date, court, start_time, end_time}
    Raises LoginError on 401, ConnectionError on 5xx or network failure.
    """
    from auth import LoginError

    params: dict = {
        "clubId": club_id,
        "date": date_str,
        "categoryCode": category_code,
        "categoryOptionsId": category_options_id,
        "timeSlotId": time_slot_id,
    }
    if court_type_code:
        params["tennisCourtTypeCode"] = court_type_code

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/availability",
                headers=_api_headers(token),
                params=params,
            ) as resp:
                if resp.status == 401:
                    raise LoginError("Auth token expired (HTTP 401).")
                if resp.status >= 500:
                    raise ConnectionError(f"API server error: HTTP {resp.status}")
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ConnectionError(f"Network error: {exc}") from exc

    slots: List[dict] = []
    for club_avail in data.get("clubsAvailabilities", []):
        court_names = {
            c["courtId"]: c.get("courtShortName") or c.get("courtName", "")
            for c in club_avail.get("courts", [])
        }
        for ts in club_avail.get("availableTimeSlots", []):
            from_min = ts.get("fromInMinutes") or ts.get("timeFromInMinutes")
            to_min = ts.get("toInMinutes") or ts.get("timeToInMinutes")
            court_id = ts.get("courtId", "")
            court = court_names.get(court_id) or ts.get("courtName") or ts.get("courtShortName", court_id)
            if from_min is not None and to_min is not None:
                slots.append({
                    "date": date_str,
                    "court": court,
                    "court_id": court_id,
                    "start_time": _minutes_to_hhmm(from_min),
                    "end_time": _minutes_to_hhmm(to_min),
                    "from_minutes": from_min,
                    "to_minutes": to_min,
                })
    return slots


def filter_slots(
    slots: List[dict],
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
) -> List[dict]:
    """Filter slots to those whose start_time falls within [time_start, time_end)."""
    if time_start is None and time_end is None:
        return slots
    result = []
    for slot in slots:
        start = slot.get("start_time", "")
        if time_start is not None and start < time_start:
            continue
        if time_end is not None and start >= time_end:
            continue
        result.append(slot)
    return result
