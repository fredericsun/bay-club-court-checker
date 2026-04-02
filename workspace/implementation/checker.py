"""
Bay Club tennis court availability checker — Sprint 3 (CLI + Config).

Extends Sprint 2 with:
- click-based CLI with --location, --court-type, --date, --time-start,
  --time-end, --interval, and --once flags
- filter_slots() to restrict results to a HH:MM time window
- YYYY-MM-DD date validation with exit code 2 on invalid input
- All CLI parameters wired through to the scraper and polling loop

Sprint 1 coverage: credential loading, Playwright login with retry, reservations
page navigation, and available court slot parsing.
Sprint 2 coverage: desktop/email notifications, async polling loop, Ctrl-C handling.
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
from typing import Callable, List, Optional

import click

from bs4 import BeautifulSoup
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOGIN_RETRIES = 3
PORTAL_LOGIN_URL = "https://members.bayclubs.com/login"
RESERVATIONS_BASE_URL = "https://members.bayclubs.com/reservations/tennis"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LoginError(Exception):
    """Raised when portal login fails after all retry attempts."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def load_credentials() -> tuple[str, str]:
    """Load BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD from environment.

    Prints a clear error message and exits with code 1 if either var is unset.
    Never logs or returns the password beyond this function boundary.
    """
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
# Login
# ---------------------------------------------------------------------------


async def login(
    username: str,
    password: str,
    page: Optional[Page] = None,
    page_fetcher: Optional[Callable] = None,
) -> Optional[Page]:
    """Log in to the Bay Club member portal with retry on transient failures.

    Args:
        username: Member email address.
        password: Member password. Never included in logs or exception messages.
        page: Playwright Page to drive (required when page_fetcher is None).
        page_fetcher: Optional async callable that replaces the live browser.
                      Signature: ``async (url, *, username, password) -> None``
                      Raise TimeoutError to simulate a retriable network failure.

    Returns:
        The authenticated Playwright Page (or the caller-supplied mock in tests).

    Raises:
        LoginError: After MAX_LOGIN_RETRIES exhausted, or on non-retriable auth
                    failure (wrong credentials, account locked, etc.).
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_LOGIN_RETRIES + 1):
        logger.debug("Login attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
        try:
            if page_fetcher is not None:
                # Injected callable — caller controls what happens (used in tests)
                await page_fetcher(PORTAL_LOGIN_URL, username=username, password=password)
                return page

            # ---- Live Playwright path ----
            await page.goto(PORTAL_LOGIN_URL, wait_until="networkidle", timeout=30_000)
            await page.fill('[name="email"]', username)
            await page.fill('[name="password"]', password)
            await page.click('[type="submit"]')
            await page.wait_for_url(
                lambda url: "login" not in url and "signin" not in url,
                timeout=15_000,
            )
            logger.info("Login succeeded on attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
            return page

        except (TimeoutError, PlaywrightTimeoutError) as exc:
            # Retriable: network timeout
            logger.warning(
                "Login attempt %d/%d timed out, retrying", attempt, MAX_LOGIN_RETRIES
            )
            last_exc = exc

        except Exception as exc:
            msg = str(exc).lower()
            if any(code in msg for code in ("500", "502", "503", "504", "server error")):
                # Retriable: transient server error
                logger.warning(
                    "Login attempt %d/%d server error, retrying", attempt, MAX_LOGIN_RETRIES
                )
                last_exc = exc
            else:
                # Non-retriable: bad credentials, account locked, etc.
                # Deliberately omit any credential values from the message.
                raise LoginError(
                    "Login failed. Check BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD."
                ) from None

    raise LoginError(
        f"Login failed after {MAX_LOGIN_RETRIES} attempts due to timeout or network error."
    ) from last_exc


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def parse_slots(
    html: str,
    date: str = "",
    location: str = "",
    court_type: str = "tennis",
) -> List[dict]:
    """Parse available court time slots from reservations page HTML.

    Designed to work identically with live portal HTML and local test fixtures.
    Never raises — returns an empty list on any parsing error.

    Args:
        html: Raw HTML string from the reservations page or a local fixture.
        date: ISO date (YYYY-MM-DD) to attach to each slot (can be empty).
        location: Location label to attach to each slot (can be empty).
        court_type: Court type label to attach to each slot.

    Returns:
        List of slot dicts with exactly these keys:
        {date, location, court_type, start_time, end_time, court_id}
        Empty list when no available slots are found or HTML is malformed.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        slots: List[dict] = []

        # ── Strategy 1: div.slot.available with data-* attributes ──────────
        for el in soup.select(".slot.available"):
            start = el.get("data-start-time") or _child_text(el, ".start-time")
            end = el.get("data-end-time") or _child_text(el, ".end-time")
            court_id = str(el.get("data-court-id", ""))
            if start and end:
                slots.append(
                    _make_slot(date, location, court_type, start, end, court_id)
                )

        # ── Strategy 2: <tr class="available"> table rows ──────────────────
        if not slots:
            for row in soup.select("tr.available"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    start = cells[0].get_text(strip=True)
                    end = cells[1].get_text(strip=True)
                    court_id = str(row.get("data-court-id", ""))
                    if start and end:
                        slots.append(
                            _make_slot(date, location, court_type, start, end, court_id)
                        )

        return slots

    except Exception:
        logger.exception("Unexpected error parsing slots; returning empty list")
        return []


def _make_slot(
    date: str,
    location: str,
    court_type: str,
    start_time: str,
    end_time: str,
    court_id: str,
) -> dict:
    return {
        "date": date,
        "location": location,
        "court_type": court_type,
        "start_time": start_time,
        "end_time": end_time,
        "court_id": court_id,
    }


def _child_text(el, selector: str) -> str:
    """Return stripped text of the first matching child element, or empty string."""
    child = el.select_one(selector)
    return child.get_text(strip=True) if child else ""


# ---------------------------------------------------------------------------
# Time window filtering
# ---------------------------------------------------------------------------


def filter_slots(
    slots: List[dict],
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
) -> List[dict]:
    """Filter slots to those whose start_time falls within [time_start, time_end).

    String comparison is correct for HH:MM 24h times (lexicographic == chronological).

    Args:
        slots: List of slot dicts from parse_slots.
        time_start: HH:MM lower bound (inclusive). None means no lower bound.
        time_end: HH:MM upper bound (exclusive). None means no upper bound.

    Returns:
        Filtered list. If both bounds are None, returns the original list unchanged.
    """
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
# Navigation
# ---------------------------------------------------------------------------


async def get_available_slots(
    page: Page,
    location: str,
    date: str,
    court_type: str = "tennis",
    page_fetcher: Optional[Callable] = None,
) -> List[dict]:
    """Navigate to the reservations page and return parsed available slots.

    Args:
        page: Authenticated Playwright Page. Ignored when page_fetcher is set.
        location: Club location name (e.g. "SF-Olympic").
        date: Date in YYYY-MM-DD format.
        court_type: Court type label passed through to parse_slots.
        page_fetcher: Optional async callable ``async (url) -> str`` returning
                      raw HTML. Replaces the live browser call in tests.

    Returns:
        List of available slot dicts.
    """
    url = f"{RESERVATIONS_BASE_URL}?location={location}&date={date}"

    if page_fetcher is not None:
        html = await page_fetcher(url)
    else:
        resp = await page.goto(url, wait_until="networkidle", timeout=30_000)
        if resp and resp.status >= 500:
            raise ConnectionError(
                f"Server returned HTTP {resp.status} for reservations page"
            )
        html = await page.content()

    return parse_slots(html, date=date, location=location, court_type=court_type)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notify_desktop(
    message: str,
    *,
    _subprocess_run: Optional[Callable] = None,
) -> None:
    """Send a macOS desktop notification via osascript.

    Falls back to stdout with '[MATCH FOUND]' prefix if osascript is
    unavailable (non-macOS or not installed).

    Args:
        message: Notification body text.
        _subprocess_run: Injectable subprocess.run replacement for testing.
    """
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


def notify_email(
    message: str,
    *,
    _smtp_factory: Optional[Callable] = None,
) -> None:
    """Send an email notification via SMTP if all required env vars are set.

    Required env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    NOTIFY_EMAIL. Returns silently if any are missing.

    Args:
        message: Email body text.
        _smtp_factory: Injectable factory replacing smtplib.SMTP for testing.
                       Signature: ``(host, port) -> context manager``.
    """
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


async def run_poll_loop(
    checker_fn: Callable,
    interval: int,
    *,
    max_polls: Optional[int] = None,
) -> None:
    """Run checker_fn in a loop, sleeping interval seconds between polls.

    Handles transient network errors (ConnectionError, TimeoutError, OSError)
    by logging a warning and continuing. Clean Ctrl-C exits with code 0.

    Args:
        checker_fn: Async callable that performs one availability check.
        interval: Seconds to sleep between polls.
        max_polls: If set, stop after this many polls (used in tests).
    """
    poll_count = 0
    try:
        while True:
            try:
                await checker_fn()
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
# Entry point (Sprint 3 — full CLI via click)
# ---------------------------------------------------------------------------


async def _run(
    location: str,
    court_type: str,
    date_str: str,
    time_start: Optional[str],
    time_end: Optional[str],
    interval: int,
    once: bool,
) -> None:
    """Core async runner; separated from CLI so it can be tested without click."""
    username, password = load_credentials()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await login(username, password, page=page)

            async def checker_fn() -> None:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                window = f"{time_start or '00:00'}–{time_end or '23:59'}"
                logger.info(
                    "[%s] Checking %s for %s on %s between %s...",
                    ts, location, court_type, date_str, window,
                )
                slots = await get_available_slots(
                    page,
                    location=location,
                    date=date_str,
                    court_type=court_type,
                )
                slots = filter_slots(slots, time_start=time_start, time_end=time_end)
                if slots:
                    for slot in slots:
                        msg = (
                            f"Court {slot['court_id']} — "
                            f"{slot['start_time']}–{slot['end_time']} "
                            f"at {slot['location']}"
                        )
                        logger.info("*** MATCH FOUND: %s ***", msg)
                        notify_desktop(msg)
                        notify_email(msg)
                else:
                    logger.info(
                        "No matching slots found. Next check in %ds.", interval
                    )

            if once:
                await checker_fn()
            else:
                await run_poll_loop(checker_fn, interval=interval)
        finally:
            await browser.close()


@click.command()
@click.option(
    "--location",
    required=True,
    help="Club location to check (e.g. SF-Olympic).",
)
@click.option(
    "--court-type",
    default="tennis",
    show_default=True,
    help="Court type to filter by (e.g. tennis, pickleball).",
)
@click.option(
    "--date",
    "date_str",
    default=None,
    metavar="YYYY-MM-DD",
    help="Date to check. Defaults to today.",
)
@click.option(
    "--time-start",
    default=None,
    metavar="HH:MM",
    help="Start of time window (24h, inclusive).",
)
@click.option(
    "--time-end",
    default=None,
    metavar="HH:MM",
    help="End of time window (24h, exclusive).",
)
@click.option(
    "--interval",
    default=300,
    show_default=True,
    type=int,
    help="Polling interval in seconds.",
)
@click.option(
    "--once",
    is_flag=True,
    default=False,
    help="Run a single check and exit immediately.",
)
def main(
    location: str,
    court_type: str,
    date_str: Optional[str],
    time_start: Optional[str],
    time_end: Optional[str],
    interval: int,
    once: bool,
) -> None:
    """Bay Club tennis court availability checker."""
    # Resolve default date
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


if __name__ == "__main__":
    main()
