"""
Bay Club tennis/pickleball court availability checker.

Uses the bayclubs.io court-booking REST API directly instead of scraping HTML.
Playwright (headless) handles login to obtain the bearer token; all subsequent
polling calls use aiohttp for speed and reliability.

Usage:
    python checker.py --location "santa clara" --court-type tennis \\
        --players Singles --duration 60 --date 2026-04-03 \\
        --time-start 07:00 --time-end 10:00 --interval 300
"""

import asyncio
import logging
import os
import smtplib
import subprocess
import sys
from datetime import date as _date
from datetime import datetime
from email.message import EmailMessage
from typing import List, Optional

import aiohttp
import click
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOGIN_RETRIES = 3
PORTAL_LOGIN_URL = "https://bayclubconnect.com/account/login/connect"
API_BASE = "https://connect-api.bayclubs.io/court-booking/api/1.0"
API_SUBSCRIPTION_KEY = "bac44a2d04b04413b6aea6d4e3aad294"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LoginError(Exception):
    """Raised when portal login fails after all retry attempts."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def load_credentials() -> tuple[str, str]:
    """Load BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD from environment."""
    username = os.environ.get("BAY_CLUB_USERNAME")
    if not username:
        print("Error: BAY_CLUB_USERNAME not set")
        sys.exit(1)
    password = os.environ.get("BAY_CLUB_PASSWORD")
    if not password:
        print("Error: BAY_CLUB_PASSWORD not set")
        sys.exit(1)
    return username, password


# ---------------------------------------------------------------------------
# Login — returns bearer token
# ---------------------------------------------------------------------------


async def login_and_get_token(username: str, password: str) -> str:
    """Log in via Playwright and return the bearer access token.

    Intercepts the /connect/token OAuth response to extract the JWT.

    Raises:
        LoginError: if login fails or the token cannot be extracted.
    """
    token: Optional[str] = None
    last_exc: Optional[Exception] = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(response):
            nonlocal token
            if "authentication2-api.bayclubs.io/connect/token" in response.url:
                try:
                    body = await response.json()
                    if "access_token" in body:
                        token = body["access_token"]
                except Exception:
                    pass

        page.on("response", on_response)

        for attempt in range(1, MAX_LOGIN_RETRIES + 1):
            logger.debug("Login attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
            try:
                await page.goto(PORTAL_LOGIN_URL, wait_until="networkidle", timeout=30_000)
                await page.fill('input[placeholder="Member ID or Username"]', username)
                await page.fill('input[placeholder="Password"]', password)
                await page.click('button:has-text("LOG IN")')
                await page.wait_for_url(lambda url: "login" not in url, timeout=15_000)
                logger.info("Login succeeded on attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
                break
            except PlaywrightTimeoutError as exc:
                logger.warning("Login attempt %d/%d timed out, retrying", attempt, MAX_LOGIN_RETRIES)
                last_exc = exc
                if attempt == MAX_LOGIN_RETRIES:
                    await browser.close()
                    raise LoginError(
                        f"Login failed after {MAX_LOGIN_RETRIES} attempts."
                    ) from last_exc

        await browser.close()

    if not token:
        raise LoginError(
            "Login succeeded but auth token was not captured. "
            "Check BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD."
        )

    return token


# ---------------------------------------------------------------------------
# Club lookup
# ---------------------------------------------------------------------------


async def resolve_club_id(token: str, location: str) -> tuple[str, str]:
    """Return (club_id, club_name) for the given location string.

    Matches case-insensitively against club name or shortName.

    Raises:
        ValueError: if no matching club is found.
    """
    headers = {"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": API_SUBSCRIPTION_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/context", headers=headers) as resp:
            data = await resp.json()

    loc_lower = location.lower()
    for club in data.get("availableClubs", []):
        if (loc_lower in club.get("name", "").lower()
                or loc_lower in club.get("shortName", "").lower()):
            return club["id"], club["name"]

    available = [c["name"] for c in data.get("availableClubs", [])]
    raise ValueError(
        f"No club found matching '{location}'. "
        f"Available clubs: {', '.join(available)}"
    )


# ---------------------------------------------------------------------------
# Filter context lookup (categoryOptionsId + timeSlotId)
# ---------------------------------------------------------------------------


async def resolve_filter_ids(
    token: str,
    club_id: str,
    category_code: str,
    players: str,
    duration_minutes: int,
) -> tuple[str, str, Optional[str]]:
    """Return (categoryOptionsId, timeSlotId, tennisCourtTypeCode).

    Raises:
        ValueError: if no matching sport/option/duration is found.
    """
    headers = {"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": API_SUBSCRIPTION_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/filterContext",
            headers=headers,
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

            # If exact duration not found, list what's available
            available_durations = [ts["durationInMinutes"] for ts in opt.get("timeSlots", [])]
            raise ValueError(
                f"Duration {duration_minutes}min not available for {category_code}/{players}. "
                f"Available: {available_durations}"
            )

    available_sports = [c["category"]["code"] for c in data.get("categories", [])]
    raise ValueError(
        f"Sport '{category_code}' not available at this club. "
        f"Available: {', '.join(available_sports)}"
    )


# ---------------------------------------------------------------------------
# Availability API
# ---------------------------------------------------------------------------


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


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

    Raises:
        LoginError: on HTTP 401 (token expired).
        ConnectionError: on HTTP 5xx or network failure.
    """
    headers = {"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": API_SUBSCRIPTION_KEY}
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
                f"{API_BASE}/availability", headers=headers, params=params
            ) as resp:
                if resp.status == 401:
                    raise LoginError("Auth token expired (HTTP 401).")
                if resp.status >= 500:
                    raise ConnectionError(f"API server error: HTTP {resp.status}")
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ConnectionError(f"Network error fetching availability: {exc}") from exc

    slots: List[dict] = []
    for club_avail in data.get("clubsAvailabilities", []):
        # Build courtId → courtName lookup from the courts list
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
                    "start_time": _minutes_to_hhmm(from_min),
                    "end_time": _minutes_to_hhmm(to_min),
                })
    return slots


# ---------------------------------------------------------------------------
# Time window filtering
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notify_desktop(message: str, *, _subprocess_run=None) -> None:
    """Send a macOS desktop notification via osascript."""
    runner = _subprocess_run if _subprocess_run is not None else subprocess.run
    script = f'display notification "{message}" with title "Bay Club Court Checker"'
    try:
        runner(["osascript", "-e", script], check=True, capture_output=True)
        logger.info("Desktop notification sent.")
    except FileNotFoundError:
        print(f"[MATCH FOUND] {message}")
    except subprocess.CalledProcessError as exc:
        logger.warning("osascript returned non-zero exit code %d", exc.returncode)
        print(f"[MATCH FOUND] {message}")


def notify_email(message: str, *, _smtp_factory=None) -> None:
    """Send an email notification via SMTP if env vars are configured."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    notify_addr = os.environ.get("NOTIFY_EMAIL")
    if not all([smtp_host, smtp_port, smtp_user, smtp_password, notify_addr]):
        return
    factory = _smtp_factory if _smtp_factory is not None else smtplib.SMTP
    msg = EmailMessage()
    msg["Subject"] = "Bay Club: Court slot available!"
    msg["From"] = smtp_user
    msg["To"] = notify_addr
    msg.set_content(message)
    with factory(smtp_host, int(smtp_port)) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)
    logger.info("Email notification sent to %s", notify_addr)


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


async def run_poll_loop(checker_fn, interval: int, *, max_polls=None) -> None:
    """Run checker_fn repeatedly, sleeping interval seconds between polls."""
    poll_count = 0
    try:
        while True:
            try:
                await checker_fn()
            except LoginError:
                raise  # bubble up — token expired, caller should re-login
            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.warning("Transient error during poll, will retry: %s", exc)
            poll_count += 1
            if max_polls is not None and poll_count >= max_polls:
                break
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Core async runner
# ---------------------------------------------------------------------------


async def _run(
    location: str,
    court_type: str,
    players: str,
    duration: int,
    date_str: str,
    time_start: Optional[str],
    time_end: Optional[str],
    interval: int,
    once: bool,
) -> None:
    username, password = load_credentials()

    logger.info("Logging in...")
    token = await login_and_get_token(username, password)

    club_id, club_name = await resolve_club_id(token, location)
    logger.info("Club: %s", club_name)

    cat_opts_id, ts_id, court_type_code = await resolve_filter_ids(
        token, club_id, court_type, players, duration
    )

    async def checker_fn() -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        window = f"{time_start or '00:00'}–{time_end or '23:59'}"
        logger.info(
            "[%s] Checking %s for %s (%s, %dmin) on %s between %s...",
            ts, club_name, court_type, players, duration, date_str, window,
        )
        slots = await fetch_available_slots(
            token, club_id, date_str, court_type,
            cat_opts_id, ts_id, court_type_code,
        )
        slots = filter_slots(slots, time_start=time_start, time_end=time_end)
        if slots:
            for slot in slots:
                msg = (
                    f"{slot['court']} — {slot['start_time']}–{slot['end_time']} "
                    f"at {club_name} on {date_str}"
                )
                logger.info("*** MATCH FOUND: %s ***", msg)
                notify_desktop(msg)
                notify_email(msg)
        else:
            logger.info("No matching slots found. Next check in %ds.", interval)

    if once:
        await checker_fn()
    else:
        await run_poll_loop(checker_fn, interval=interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--location", default="santa clara", show_default=True,
    help="Club location to check (e.g. 'santa clara', 'redwood shores').",
)
@click.option(
    "--court-type", default="tennis", show_default=True,
    help="Sport: tennis, pickleball, or squash.",
)
@click.option(
    "--players", default="Singles", show_default=True,
    help="Player type: Singles, Doubles, or Ball machine.",
)
@click.option(
    "--duration", default=60, show_default=True, type=int,
    help="Session duration in minutes (30, 60, or 90).",
)
@click.option(
    "--date", "date_str", default=None, metavar="YYYY-MM-DD",
    help="Date to check. Defaults to today.",
)
@click.option(
    "--time-start", default=None, metavar="HH:MM",
    help="Start of time window (24h, inclusive).",
)
@click.option(
    "--time-end", default=None, metavar="HH:MM",
    help="End of time window (24h, exclusive).",
)
@click.option(
    "--interval", default=300, show_default=True, type=int,
    help="Polling interval in seconds.",
)
@click.option(
    "--once", is_flag=True, default=False,
    help="Run a single check and exit immediately.",
)
def main(
    location: str,
    court_type: str,
    players: str,
    duration: int,
    date_str: Optional[str],
    time_start: Optional[str],
    time_end: Optional[str],
    interval: int,
    once: bool,
) -> None:
    """Bay Club court availability checker."""
    if date_str is None:
        date_str = _date.today().isoformat()
    else:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise click.UsageError("Invalid date format. Use YYYY-MM-DD.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        asyncio.run(
            _run(
                location=location,
                court_type=court_type,
                players=players,
                duration=duration,
                date_str=date_str,
                time_start=time_start,
                time_end=time_end,
                interval=interval,
                once=once,
            )
        )
    except LoginError:
        print("Error: Login failed. Check BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD.")
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
