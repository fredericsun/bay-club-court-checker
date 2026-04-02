"""Sprint 2 acceptance tests: Notification + Polling.

Run from workspace/implementation/:
    pytest tests/test_sprint2.py -v
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import checker
from checker import notify_desktop, notify_email, run_poll_loop


# ---------------------------------------------------------------------------
# notify_desktop
# ---------------------------------------------------------------------------


def test_notify_desktop_calls_osascript_on_success():
    """notify_desktop completes without raising when osascript exits 0."""
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    notify_desktop("Court 3 — 08:00–09:00", _subprocess_run=mock_run)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "osascript"


def test_notify_desktop_no_exception_on_success():
    """notify_desktop with a zero-exit mock does not raise."""
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    # Should not raise
    notify_desktop("test message", _subprocess_run=mock_run)


def test_notify_desktop_fallback_to_stdout_when_osascript_missing(capsys):
    """notify_desktop falls back to [MATCH FOUND] stdout when osascript is not found."""

    def raise_file_not_found(cmd, **kwargs):
        raise FileNotFoundError("osascript not found")

    notify_desktop("Court 1 available", _subprocess_run=raise_file_not_found)
    captured = capsys.readouterr()
    assert "[MATCH FOUND]" in captured.out
    assert "Court 1 available" in captured.out


def test_notify_desktop_fallback_to_stdout_on_nonzero_exit(capsys):
    """notify_desktop falls back to stdout when osascript returns non-zero."""

    def raise_called_process_error(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    notify_desktop("some slot", _subprocess_run=raise_called_process_error)
    captured = capsys.readouterr()
    assert "[MATCH FOUND]" in captured.out


# ---------------------------------------------------------------------------
# notify_email
# ---------------------------------------------------------------------------


def _smtp_env():
    return {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user@example.com",
        "SMTP_PASSWORD": "secret",
        "NOTIFY_EMAIL": "notify@example.com",
    }


def test_notify_email_calls_smtp_and_sends_one_message(monkeypatch):
    """notify_email calls the factory and sends exactly one message."""
    for k, v in _smtp_env().items():
        monkeypatch.setenv(k, v)

    mock_smtp_instance = MagicMock()
    mock_smtp_cm = MagicMock()
    mock_smtp_cm.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_cm.__exit__ = MagicMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_smtp_cm)

    notify_email("Court 3 available", _smtp_factory=mock_factory)

    mock_factory.assert_called_once_with("smtp.example.com", 587)
    mock_smtp_instance.send_message.assert_called_once()


def test_notify_email_skips_when_smtp_host_missing(monkeypatch):
    """notify_email returns without calling SMTP if SMTP_HOST is absent."""
    for k, v in _smtp_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SMTP_HOST")

    mock_factory = MagicMock()
    notify_email("test", _smtp_factory=mock_factory)
    mock_factory.assert_not_called()


def test_notify_email_skips_when_notify_email_missing(monkeypatch):
    """notify_email returns without calling SMTP if NOTIFY_EMAIL is absent."""
    for k, v in _smtp_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("NOTIFY_EMAIL")

    mock_factory = MagicMock()
    notify_email("test", _smtp_factory=mock_factory)
    mock_factory.assert_not_called()


def test_notify_email_skips_when_no_smtp_vars_set(monkeypatch):
    """notify_email returns silently when no SMTP env vars are set."""
    for key in _smtp_env():
        monkeypatch.delenv(key, raising=False)

    mock_factory = MagicMock()
    notify_email("test", _smtp_factory=mock_factory)
    mock_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_loop_calls_checker_three_times():
    """Polling loop calls checker_fn exactly 3 times and sleeps between each."""
    checker_mock = AsyncMock(return_value=[])

    with patch("checker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await run_poll_loop(checker_mock, interval=60, max_polls=3)

    assert checker_mock.call_count == 3
    # Sleep is called between polls: 2 times for 3 polls (after poll 1 and 2)
    assert mock_sleep.call_count == 2
    for c in mock_sleep.call_args_list:
        assert c == call(60)


@pytest.mark.asyncio
async def test_polling_loop_continues_on_connection_error(caplog):
    """Polling loop logs a warning and continues when checker raises ConnectionError."""
    import logging

    call_count = 0

    async def flaky_checker():
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ConnectionError("simulated connection reset")

    with patch("checker.asyncio.sleep", new_callable=AsyncMock):
        with caplog.at_level(logging.WARNING, logger="checker"):
            await run_poll_loop(flaky_checker, interval=1, max_polls=3)

    assert call_count == 3
    assert any("Transient error" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_polling_loop_clean_exit_on_keyboard_interrupt(capsys):
    """Polling loop prints 'Stopped.' and exits with code 0 on KeyboardInterrupt."""
    call_count = 0

    async def checker_that_interrupts():
        nonlocal call_count
        call_count += 1
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc_info:
        await run_poll_loop(checker_that_interrupts, interval=1)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Stopped." in captured.out


@pytest.mark.asyncio
async def test_polling_loop_sleep_uses_configured_interval():
    """asyncio.sleep is called with exactly the configured interval value."""
    checker_mock = AsyncMock(return_value=[])

    with patch("checker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await run_poll_loop(checker_mock, interval=300, max_polls=2)

    for c in mock_sleep.call_args_list:
        assert c == call(300)


@pytest.mark.asyncio
async def test_polling_loop_no_sleep_after_last_poll():
    """Sleep is NOT called after the final poll (between-polls semantics)."""
    checker_mock = AsyncMock(return_value=[])

    with patch("checker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await run_poll_loop(checker_mock, interval=10, max_polls=1)

    # max_polls=1 → one check, no sleep
    assert checker_mock.call_count == 1
    assert mock_sleep.call_count == 0
