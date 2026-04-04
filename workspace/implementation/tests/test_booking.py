# workspace/implementation/tests/test_booking.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from booking import book_slot


@pytest.mark.asyncio
async def test_book_slot_returns_true_on_success():
    slot = {
        "date": "2026-04-05",
        "court": "Court 9",
        "court_id": "03823d50-ccba-48b4-20ab-08d8221b2fbc",
        "start_time": "07:00",
        "end_time": "08:00",
        "from_minutes": 420,
        "to_minutes": 480,
    }
    mock_response = AsyncMock()
    mock_response.status = 201
    mock_response.json = AsyncMock(return_value={"bookingId": "abc-123"})

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_post)

    with patch("booking.aiohttp.ClientSession", return_value=mock_session):
        result = await book_slot(
            token="test-token",
            club_id="3bc78448-ec6b-49e1-a2ae-64abd68e646b",
            slot=slot,
            category_options_id="51d556a3-ef65-4d50-a37a-8843d89b8aa0",
            time_slot_id="89a1327a-c893-49f6-88a9-be4c9ab4d481",
            court_type_code="outdoor",
        )
    assert result is True


@pytest.mark.asyncio
async def test_book_slot_raises_login_error_on_401():
    from auth import LoginError
    slot = {
        "date": "2026-04-05", "court": "Court 9",
        "court_id": "03823d50-ccba-48b4-20ab-08d8221b2fbc",
        "start_time": "07:00", "end_time": "08:00",
        "from_minutes": 420, "to_minutes": 480,
    }
    mock_response = AsyncMock()
    mock_response.status = 401

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_post)

    with patch("booking.aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(LoginError):
            await book_slot(
                token="expired",
                club_id="3bc78448-ec6b-49e1-a2ae-64abd68e646b",
                slot=slot,
                category_options_id="opt-id",
                time_slot_id="ts-id",
                court_type_code="outdoor",
            )


@pytest.mark.asyncio
async def test_book_slot_raises_runtime_error_on_400():
    slot = {
        "date": "2026-04-05", "court": "Court 9",
        "court_id": "03823d50-ccba-48b4-20ab-08d8221b2fbc",
        "start_time": "07:00", "end_time": "08:00",
        "from_minutes": 420, "to_minutes": 480,
    }
    mock_response = AsyncMock()
    mock_response.status = 400
    mock_response.text = AsyncMock(return_value='{"Errors":[{"Code":"MaxDailyBookingsCountPerMembersipExceeded"}]}')

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_post)

    with patch("booking.aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(RuntimeError):
            await book_slot(
                token="token",
                club_id="club-id",
                slot=slot,
                category_options_id="opt-id",
                time_slot_id="ts-id",
                court_type_code="outdoor",
            )


@pytest.mark.asyncio
async def test_book_slot_sends_correct_request_body():
    """Verify the request body has the correct fields including date object format."""
    slot = {
        "date": "2026-04-05",
        "court": "Court 9",
        "court_id": "court-uuid-123",
        "start_time": "07:00",
        "end_time": "08:00",
        "from_minutes": 420,
        "to_minutes": 480,
    }
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"bookingId": "xyz"})

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_post)

    with patch("booking.aiohttp.ClientSession", return_value=mock_session):
        result = await book_slot(
            token="tok",
            club_id="club-abc",
            slot=slot,
            category_options_id="cat-opt-id",
            time_slot_id="ts-id",
            court_type_code="outdoor",
        )

    assert result is True
    call_kwargs = mock_session.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert sent_json["clubId"] == "club-abc"
    assert sent_json["date"] == {"value": "2026-04-05", "date": "2026-04-05"}
    assert sent_json["timeFromInMinutes"] == 420
    assert sent_json["timeToInMinutes"] == 480
    assert sent_json["courtId"] == "court-uuid-123"
    assert sent_json["categoryOptionsId"] == "cat-opt-id"
    assert sent_json["timeSlotId"] == "ts-id"
    assert sent_json["tennisCourtTypeCode"] == "outdoor"
