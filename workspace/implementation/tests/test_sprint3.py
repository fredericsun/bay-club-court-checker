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
        "--date",
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
    """`--date not-a-date` exits with code 2."""
    runner = CliRunner()
    result = runner.invoke(main, ["--location", "SF-Olympic", "--date", "not-a-date"])
    assert result.exit_code == 2


def test_invalid_date_prints_error_message():
    """`--date not-a-date` prints 'Error: Invalid date format'."""
    runner = CliRunner()
    result = runner.invoke(main, ["--location", "SF-Olympic", "--date", "not-a-date"])
    assert "Invalid date format" in result.output


def test_valid_date_accepted():
    """`--date 2026-04-05` is accepted (no validation error)."""
    runner = CliRunner()
    # Patch _run so we don't actually hit the network
    with patch("checker.asyncio.run"):
        result = runner.invoke(main, ["--location", "SF-Olympic", "--date", "2026-04-05"])
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
    """_run with once=True invokes get_available_slots exactly once."""
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.setenv("BAY_CLUB_PASSWORD", "secret")

    get_slots_calls = []

    async def mock_get_slots(page, location, date, court_type="tennis", **kwargs):
        get_slots_calls.append({"location": location})
        return []

    mock_login = AsyncMock()
    mock_async_playwright = _make_async_playwright_mock(mock_get_slots)

    monkeypatch.setattr("checker.login", mock_login)
    monkeypatch.setattr("checker.get_available_slots", mock_get_slots)
    monkeypatch.setattr("checker.async_playwright", mock_async_playwright)

    await checker._run(
        location="SF-Olympic",
        court_type="tennis",
        date_str="2026-04-05",
        time_start=None,
        time_end=None,
        interval=300,
        once=True,
    )

    assert len(get_slots_calls) == 1


# ---------------------------------------------------------------------------
# --location is passed to the scraper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_location_passed_to_scraper(monkeypatch):
    """_run passes the --location value through to get_available_slots."""
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.setenv("BAY_CLUB_PASSWORD", "secret")

    captured_location = []

    async def mock_get_slots(page, location, date, court_type="tennis", **kwargs):
        captured_location.append(location)
        return []

    mock_async_playwright = _make_async_playwright_mock(mock_get_slots)
    monkeypatch.setattr("checker.login", AsyncMock())
    monkeypatch.setattr("checker.get_available_slots", mock_get_slots)
    monkeypatch.setattr("checker.async_playwright", mock_async_playwright)

    await checker._run(
        location="SF-Olympic",
        court_type="tennis",
        date_str="2026-04-05",
        time_start=None,
        time_end=None,
        interval=300,
        once=True,
    )

    assert captured_location == ["SF-Olympic"]


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
