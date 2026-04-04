"""Sprint 3 acceptance tests: CLI + Config.

Run from workspace/implementation/:
    pytest tests/test_sprint3.py -v
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent))
import checker
from checker import filter_slots, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_SLOTS = [
    {
        "date": "2026-04-05",
        "location": "SF-Olympic",
        "court_type": "tennis",
        "start_time": "07:00",
        "end_time": "08:00",
        "court_id": "1",
    },
    {
        "date": "2026-04-05",
        "location": "SF-Olympic",
        "court_type": "tennis",
        "start_time": "08:00",
        "end_time": "09:00",
        "court_id": "2",
    },
    {
        "date": "2026-04-05",
        "location": "SF-Olympic",
        "court_type": "tennis",
        "start_time": "09:00",
        "end_time": "10:00",
        "court_id": "3",
    },
    {
        "date": "2026-04-05",
        "location": "SF-Olympic",
        "court_type": "tennis",
        "start_time": "10:00",
        "end_time": "11:00",
        "court_id": "4",
    },
]


def _make_async_playwright_mock(get_slots_mock):
    """Return a patched async_playwright that stubs out the browser."""
    mock_page = AsyncMock()
    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    mock_pw = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_pw_cm)


# ---------------------------------------------------------------------------
# --help output
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_contains_all_flags():
    """`--help` exits 0 and lists every documented flag."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for flag in [
        "--location",
        "--court-type",
        "--mode",
        "--from",
        "--to",
        "--max-bookings",
        "--time-start",
        "--time-end",
        "--interval",
        "--once",
    ]:
        assert flag in result.output, f"Missing flag in --help output: {flag}"


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------


def test_invalid_date_exits_code_2():
    """`--from not-a-date` exits with code 2."""
    runner = CliRunner()
    result = runner.invoke(main, [
        "--mode", "notify", "--location", "SF-Olympic",
        "--from", "not-a-date", "--to", "2026-12-31",
    ])
    assert result.exit_code == 2


def test_invalid_date_prints_error_message():
    """`--from not-a-date` prints 'Error: Invalid date format'."""
    runner = CliRunner()
    result = runner.invoke(main, [
        "--mode", "notify", "--location", "SF-Olympic",
        "--from", "not-a-date", "--to", "2026-12-31",
    ])
    assert "Invalid" in result.output


def test_valid_date_accepted():
    """`--from 2026-04-05 --to 2026-04-05` is accepted (no validation error)."""
    runner = CliRunner()
    # Patch _run so we don't actually hit the network
    with patch("checker.asyncio.run"):
        result = runner.invoke(main, [
            "--mode", "notify", "--location", "SF-Olympic",
            "--from", "2026-04-05", "--to", "2026-04-05",
        ])
    # Exit code may be 0 (asyncio.run mocked) or from load_credentials; just not 2
    assert result.exit_code != 2


# ---------------------------------------------------------------------------
# filter_slots
# ---------------------------------------------------------------------------


def test_filter_slots_by_time_window():
    """filter_slots returns only slots with start_time in [08:00, 10:00)."""
    filtered = filter_slots(_SAMPLE_SLOTS, time_start="08:00", time_end="10:00")
    start_times = [s["start_time"] for s in filtered]
    assert start_times == ["08:00", "09:00"]


def test_filter_slots_no_bounds_returns_all():
    """filter_slots with both bounds None returns the original list unchanged."""
    result = filter_slots(_SAMPLE_SLOTS, time_start=None, time_end=None)
    assert result == _SAMPLE_SLOTS


def test_filter_slots_only_start_bound():
    """filter_slots with only time_start filters correctly."""
    filtered = filter_slots(_SAMPLE_SLOTS, time_start="09:00", time_end=None)
    assert all(s["start_time"] >= "09:00" for s in filtered)
    assert len(filtered) == 2  # 09:00 and 10:00


def test_filter_slots_only_end_bound():
    """filter_slots with only time_end filters correctly."""
    filtered = filter_slots(_SAMPLE_SLOTS, time_start=None, time_end="09:00")
    assert all(s["start_time"] < "09:00" for s in filtered)
    assert len(filtered) == 2  # 07:00 and 08:00


def test_filter_slots_empty_input():
    """filter_slots with an empty list returns an empty list."""
    assert filter_slots([], time_start="08:00", time_end="10:00") == []


# ---------------------------------------------------------------------------
# --once flag: exactly one check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_once_flag_calls_get_slots_exactly_once(monkeypatch):
    """_run with once=True invokes fetch_available_slots exactly once per date."""
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.setenv("BAY_CLUB_PASSWORD", "secret")

    fetch_calls = []

    async def mock_fetch(token, club_id, date_str, *args, **kwargs):
        fetch_calls.append(date_str)
        return []

    with patch("checker.fetch_available_slots", side_effect=mock_fetch), \
         patch("checker.login_and_get_token", return_value="tok"), \
         patch("checker.resolve_club_id", return_value=("club-id", "SF-Olympic")), \
         patch("checker.resolve_filter_ids", return_value=("opt-id", "ts-id", "outdoor")):
        await checker._run(
            mode="notify",
            from_date=date(2026, 4, 5),
            to_date=date(2026, 4, 5),
            max_bookings=1,
            location="SF-Olympic",
            court_type="tennis",
            players="Singles",
            duration=60,
            time_start=None,
            time_end=None,
            interval=300,
            once=True,
        )

    assert len(fetch_calls) == 1


# ---------------------------------------------------------------------------
# --location is passed to the scraper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_location_passed_to_scraper(monkeypatch):
    """_run passes the --location value through to resolve_club_id."""
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.setenv("BAY_CLUB_PASSWORD", "secret")

    captured_locations = []

    async def mock_resolve_club_id(token, location):
        captured_locations.append(location)
        return ("club-id", location)

    with patch("checker.fetch_available_slots", return_value=[]), \
         patch("checker.login_and_get_token", return_value="tok"), \
         patch("checker.resolve_club_id", side_effect=mock_resolve_club_id), \
         patch("checker.resolve_filter_ids", return_value=("opt-id", "ts-id", "outdoor")):
        await checker._run(
            mode="notify",
            from_date=date(2026, 4, 5),
            to_date=date(2026, 4, 5),
            max_bookings=1,
            location="SF-Olympic",
            court_type="tennis",
            players="Singles",
            duration=60,
            time_start=None,
            time_end=None,
            interval=300,
            once=True,
        )

    assert captured_locations == ["SF-Olympic"]


# --- date_range tests ---
from datetime import date, timedelta
from availability import date_range


def test_date_range_basic():
    today = date.today()
    result = date_range(today, today + timedelta(days=2))
    assert result == [today, today + timedelta(days=1), today + timedelta(days=2)]


def test_date_range_caps_at_3_days_ahead():
    today = date.today()
    result = date_range(today, today + timedelta(days=10))
    assert result == [
        today,
        today + timedelta(days=1),
        today + timedelta(days=2),
        today + timedelta(days=3),
    ]


def test_date_range_from_in_past_starts_today():
    today = date.today()
    yesterday = today - timedelta(days=1)
    result = date_range(yesterday, today + timedelta(days=1))
    assert result == [today, today + timedelta(days=1)]


def test_date_range_empty_when_to_before_today():
    today = date.today()
    yesterday = today - timedelta(days=1)
    result = date_range(yesterday, yesterday)
    assert result == []


# --- notify mode test ---
from unittest.mock import AsyncMock, patch, MagicMock
import pytest


@pytest.mark.asyncio
async def test_notify_mode_iterates_all_dates_in_range():
    """notify mode calls fetch_available_slots once per date in range."""
    today = date.today()
    dates_checked = []

    async def mock_fetch(token, club_id, date_str, *args, **kwargs):
        dates_checked.append(date_str)
        return []

    with patch("checker.fetch_available_slots", side_effect=mock_fetch), \
         patch("checker.login_and_get_token", return_value="tok"), \
         patch("checker.resolve_club_id", return_value=("club-id", "Bay Club Santa Clara")), \
         patch("checker.resolve_filter_ids", return_value=("opt-id", "ts-id", "outdoor")):
        from checker import _run
        await _run(
            mode="notify",
            from_date=today,
            to_date=today + timedelta(days=2),
            max_bookings=1,
            location="santa clara",
            court_type="tennis",
            players="Singles",
            duration=60,
            time_start=None,
            time_end=None,
            interval=300,
            once=True,
        )

    assert len(dates_checked) == 3
    assert dates_checked[0] == today.isoformat()
    assert dates_checked[2] == (today + timedelta(days=2)).isoformat()
