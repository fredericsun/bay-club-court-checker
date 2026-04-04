"""Sprint 1 acceptance tests.

Run from workspace/implementation/:
    pytest tests/test_sprint1.py -v
"""

import os
import sys
from pathlib import Path

import pytest

# Make checker importable without installing
sys.path.insert(0, str(Path(__file__).parent.parent))
import checker
from auth import LoginError, MAX_LOGIN_RETRIES, load_credentials

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------


def test_missing_username_exits_with_code_1(monkeypatch, capsys):
    """Running with BAY_CLUB_USERNAME unset must print the error and exit 1."""
    monkeypatch.delenv("BAY_CLUB_USERNAME", raising=False)
    monkeypatch.delenv("BAY_CLUB_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        load_credentials()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "BAY_CLUB_USERNAME" in (captured.out + captured.err)


def test_missing_password_exits_with_code_1(monkeypatch, capsys):
    """Running with BAY_CLUB_PASSWORD unset must print the error and exit 1."""
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.delenv("BAY_CLUB_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        load_credentials()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "BAY_CLUB_PASSWORD" in (captured.out + captured.err)


def test_load_credentials_returns_tuple(monkeypatch):
    monkeypatch.setenv("BAY_CLUB_USERNAME", "user@example.com")
    monkeypatch.setenv("BAY_CLUB_PASSWORD", "s3cr3t")
    username, password = load_credentials()
    assert username == "user@example.com"
    assert password == "s3cr3t"


# ---------------------------------------------------------------------------
# parse_slots — fixture with available slots
# ---------------------------------------------------------------------------


def test_parse_slots_fixture_returns_slots():
    """parse_slots(html) with the bundled fixture returns >= 1 dicts."""
    html = (FIXTURES / "reservations.html").read_text()
    slots = parse_slots(html)
    assert len(slots) >= 1


def test_parse_slots_fixture_has_required_keys():
    """Each returned slot must contain all required keys."""
    required_keys = {"date", "location", "court_type", "start_time", "end_time", "court_id"}
    html = (FIXTURES / "reservations.html").read_text()
    slots = parse_slots(html)
    for slot in slots:
        assert required_keys.issubset(slot.keys()), f"Missing keys in slot: {slot}"


def test_parse_slots_only_returns_available_slots():
    """Booked slots must not appear in the output."""
    html = (FIXTURES / "reservations.html").read_text()
    slots = parse_slots(html)
    # Fixture has 3 available and 1 booked slot
    assert len(slots) == 3


def test_parse_slots_attaches_date_location_court_type():
    html = (FIXTURES / "reservations.html").read_text()
    slots = parse_slots(html, date="2026-04-05", location="SF-Olympic", court_type="tennis")
    for slot in slots:
        assert slot["date"] == "2026-04-05"
        assert slot["location"] == "SF-Olympic"
        assert slot["court_type"] == "tennis"


# ---------------------------------------------------------------------------
# parse_slots — empty fixture
# ---------------------------------------------------------------------------


def test_parse_slots_empty_fixture_returns_empty_list():
    """parse_slots with a no-slots fixture returns [] without raising."""
    html = (FIXTURES / "empty_reservations.html").read_text()
    slots = parse_slots(html)
    assert slots == []


def test_parse_slots_malformed_html_returns_empty_list():
    """parse_slots on garbage input returns [] without raising."""
    slots = parse_slots("<html><body>no slots here</body></html>")
    assert slots == []


def test_parse_slots_empty_string_returns_empty_list():
    slots = parse_slots("")
    assert slots == []


# ---------------------------------------------------------------------------
# Login retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_retries_exactly_max_times_on_timeout():
    """Login must retry exactly MAX_LOGIN_RETRIES times then raise LoginError."""
    call_count = 0

    async def always_timeout(url, *, username, password):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("simulated network timeout")

    with pytest.raises(LoginError):
        await checker.login(
            username="user@example.com",
            password="password123",
            page=None,
            page_fetcher=always_timeout,
        )

    assert call_count == MAX_LOGIN_RETRIES


@pytest.mark.asyncio
async def test_login_succeeds_if_fetcher_does_not_raise():
    """Login returns the page mock when the fetcher succeeds."""
    mock_page = object()

    async def success_fetcher(url, *, username, password):
        pass  # no exception = success

    result = await checker.login(
        username="user@example.com",
        password="password123",
        page=mock_page,
        page_fetcher=success_fetcher,
    )
    assert result is mock_page


@pytest.mark.asyncio
async def test_login_retries_then_succeeds():
    """Login succeeds on the 3rd attempt after 2 timeouts."""
    call_count = 0

    async def flaky_fetcher(url, *, username, password):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("temporary timeout")

    mock_page = object()
    result = await checker.login(
        username="user@example.com",
        password="password123",
        page=mock_page,
        page_fetcher=flaky_fetcher,
    )
    assert result is mock_page
    assert call_count == 3


# ---------------------------------------------------------------------------
# No credentials in log / exception output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_password_in_login_error():
    """The LoginError message must not contain the password."""
    password = "super_secret_password_99999"

    async def always_timeout(url, *, username, password):
        raise TimeoutError("timeout")

    with pytest.raises(LoginError) as exc_info:
        await checker.login(
            username="user@example.com",
            password=password,
            page_fetcher=always_timeout,
        )

    error_text = str(exc_info.value) + repr(exc_info.value)
    assert password not in error_text


@pytest.mark.asyncio
async def test_no_username_in_login_error():
    """The LoginError message must not contain the username."""
    username = "unique_user_identifier_12345@example.com"

    async def always_timeout(url, **kwargs):
        raise TimeoutError("timeout")

    with pytest.raises(LoginError) as exc_info:
        await checker.login(
            username=username,
            password="pass",
            page_fetcher=always_timeout,
        )

    assert username not in str(exc_info.value)
