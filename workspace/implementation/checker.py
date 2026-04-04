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
import sys
from datetime import date as _date
from datetime import datetime
from typing import List, Optional

import click
from dotenv import load_dotenv

from auth import LoginError, load_credentials, login_and_get_token
from availability import (
    resolve_club_id,
    resolve_filter_ids,
    fetch_available_slots,
    filter_slots,
    date_range,
)
from booking import book_slot
from notifier import notify_desktop, notify_email

load_dotenv()
logger = logging.getLogger(__name__)


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
    mode: str,
    from_date,
    to_date,
    max_bookings: int,
    location: str,
    court_type: str,
    players: str,
    duration: int,
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

    bookings_made = 0

    async def poll_once() -> None:
        nonlocal token, bookings_made
        dates = date_range(from_date, to_date)
        if not dates:
            logger.info("Date range has expired. Exiting.")
            raise SystemExit(0)

        for d in dates:
            date_str = d.isoformat()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            window = f"{time_start or '00:00'}–{time_end or '23:59'}"
            logger.info(
                "[%s] Checking %s for %s on %s between %s...",
                ts, club_name, court_type, date_str, window,
            )

            try:
                slots = await fetch_available_slots(
                    token, club_id, date_str, court_type, cat_opts_id, ts_id, court_type_code
                )
            except LoginError:
                logger.info("Token expired, re-logging in...")
                token = await login_and_get_token(username, password)
                slots = await fetch_available_slots(
                    token, club_id, date_str, court_type, cat_opts_id, ts_id, court_type_code
                )

            slots = filter_slots(slots, time_start=time_start, time_end=time_end)

            if mode == "notify":
                for slot in slots:
                    msg = (
                        f"{slot['court']} — {slot['start_time']}–{slot['end_time']} "
                        f"at {club_name} on {date_str}"
                    )
                    logger.info("*** MATCH FOUND: %s ***", msg)
                    notify_desktop(msg)
                    notify_email(msg)
                if not slots:
                    logger.info("No matching slots found for %s.", date_str)

            elif mode == "autobook":
                for slot in slots:
                    logger.info(
                        "Attempting to book %s %s–%s...",
                        date_str, slot["start_time"], slot["end_time"],
                    )
                    try:
                        await book_slot(
                            token, club_id, slot, cat_opts_id, ts_id, court_type_code
                        )
                        bookings_made += 1
                        msg = (
                            f"{slot['court']} — {slot['start_time']}–{slot['end_time']} "
                            f"at {club_name} on {date_str} (BOOKED)"
                        )
                        logger.info("*** BOOKED: %s ***", msg)
                        notify_desktop(msg)
                        notify_email(msg)
                        if bookings_made >= max_bookings:
                            logger.info("Reached max bookings (%d). Exiting.", max_bookings)
                            raise SystemExit(0)
                        break  # one booking per date per poll cycle
                    except (ConnectionError, RuntimeError) as exc:
                        logger.warning("Booking failed: %s", exc)

    if once:
        await poll_once()
    else:
        await run_poll_loop(poll_once, interval=interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--mode", type=click.Choice(["notify", "autobook"]), required=True,
    help="Operating mode: notify (alert only) or autobook (book automatically).",
)
@click.option(
    "--from", "from_date", default=None, metavar="YYYY-MM-DD",
    help="Start of date range. Defaults to today.",
)
@click.option(
    "--to", "to_date", required=True, metavar="YYYY-MM-DD",
    help="End of date range (inclusive).",
)
@click.option(
    "--max-bookings", default=1, show_default=True, type=int,
    help="Auto-book mode: stop after N successful bookings.",
)
@click.option(
    "--location", default="santa clara", show_default=True,
    help="Club location (e.g. 'santa clara', 'redwood shores').",
)
@click.option(
    "--court-type", default="tennis", show_default=True,
    help="Sport: tennis, pickleball, or squash.",
)
@click.option(
    "--players", default="Singles", show_default=True,
    help="Singles, Doubles, or Ball machine.",
)
@click.option(
    "--duration", default=60, show_default=True, type=int,
    help="Session duration in minutes: 30, 60, or 90.",
)
@click.option(
    "--date", "date_str", default=None, metavar="YYYY-MM-DD",
    help="(Deprecated) Date to check. Use --from/--to instead.",
    hidden=True,
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
    help="Seconds between polls.",
)
@click.option(
    "--once", is_flag=True, default=False,
    help="Run one poll cycle across all dates, then exit.",
)
def main(
    mode: str,
    from_date: Optional[str],
    to_date: str,
    max_bookings: int,
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
    today = _date.today()

    if from_date is None:
        from_date_parsed = today
    else:
        try:
            from_date_parsed = _date.fromisoformat(from_date)
        except ValueError:
            raise click.UsageError("Invalid --from date. Use YYYY-MM-DD.")

    try:
        to_date_parsed = _date.fromisoformat(to_date)
    except ValueError:
        raise click.UsageError("Invalid --to date. Use YYYY-MM-DD.")

    if to_date_parsed < today:
        raise click.UsageError("--to date is in the past.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        asyncio.run(
            _run(
                mode=mode,
                from_date=from_date_parsed,
                to_date=to_date_parsed,
                max_bookings=max_bookings,
                location=location,
                court_type=court_type,
                players=players,
                duration=duration,
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
