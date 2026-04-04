# Long-Running Modes Design

**Date:** 2026-04-03  
**Status:** Approved

## Overview

Extend the Bay Club court checker from a single-date polling script into a long-running app with two distinct operating modes:

1. **Notify mode** — polls a date range and notifies the user whenever a matching slot appears
2. **Auto-book mode** — polls a date range and automatically books matching slots up to a configurable limit

## Architecture

Split the current monolithic `checker.py` into focused modules:

```
workspace/implementation/
├── checker.py        — CLI entry point + polling orchestration
├── auth.py           — login_and_get_token, load_credentials
├── availability.py   — resolve_club_id, resolve_filter_ids, fetch_available_slots, filter_slots
├── booking.py        — book_slot (API to be reverse-engineered during implementation)
└── notifier.py       — notify_desktop, notify_email
```

No state persistence. Duplicate notifications across poll cycles are acceptable.

## Modes

### Notify Mode

Each poll cycle:
1. Iterate over all dates in `[max(from, today), min(to, today+3)]`
2. Call `fetch_available_slots` for each date
3. Apply time window filter (`--time-start` / `--time-end`)
4. Notify for every matching slot found
5. Sleep `--interval` seconds and repeat

Exits when `--to` date is passed or user presses Ctrl-C.

### Auto-Book Mode

Each poll cycle:
1. Same date iteration and filtering as notify mode (dates in chronological order)
2. On first matching slot found (earliest date, then earliest start time): call `book_slot`
3. On success: notify user, increment in-memory `bookings_made` counter
4. Stop when `bookings_made == --max-bookings`
5. On booking failure: log error, do not increment counter, retry next poll

## CLI

### New flags

| Flag | Mode | Description | Default |
|------|------|-------------|---------|
| `--mode` | both | `notify` or `autobook` | required |
| `--from` | both | Start date `YYYY-MM-DD` | today |
| `--to` | both | End date `YYYY-MM-DD` | required |
| `--max-bookings` | autobook | Stop after N successful bookings | 1 |

### Existing flags (unchanged)

`--location`, `--court-type`, `--players`, `--duration`, `--time-start`, `--time-end`, `--interval`, `--once` (runs one poll cycle across all dates in range, then exits)

### Example invocations

```bash
# Notify mode — alert on any evening slots in April
python checker.py --mode notify \
  --from 2026-04-05 --to 2026-04-30 \
  --time-start 18:00 --time-end 21:00

# Auto-book mode — book 2 morning slots this weekend
python checker.py --mode autobook \
  --from 2026-04-04 --to 2026-04-06 \
  --time-start 07:00 --time-end 10:00 \
  --max-bookings 2
```

## Booking API

The booking endpoint is unknown and must be reverse-engineered during implementation using the same Playwright interception technique used to discover the availability API. Expected to be a two-step flow:

1. `POST /court-booking/api/1.0/courtbookings/temporary` — reserve a slot
2. `POST /court-booking/api/1.0/courtbookings` or `PUT .../confirm` — confirm

`booking.py` exposes:

```python
async def book_slot(token: str, club_id: str, slot: dict, ...) -> bool
```

Returns `True` on success, raises on failure.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Bearer token expired (HTTP 401) | Re-login automatically, retry |
| Network error / API 5xx | Log warning, continue polling |
| No slots found | Silent, wait for next interval |
| Booking fails | Log error, don't count toward `--max-bookings`, retry next poll |
| `--to` date passed | Exit cleanly with summary |
| Ctrl-C | Print "Stopped." exit code 0 |

## Module Responsibilities

### `auth.py`
- `load_credentials() -> (username, password)`
- `login_and_get_token(username, password) -> str`

### `availability.py`
- `resolve_club_id(token, location) -> (club_id, club_name)`
- `resolve_filter_ids(token, club_id, category_code, players, duration) -> (cat_opts_id, ts_id, court_type_code)`
- `fetch_available_slots(token, club_id, date_str, ...) -> List[dict]`
- `filter_slots(slots, time_start, time_end) -> List[dict]`

### `booking.py`
- `book_slot(token, club_id, slot, ...) -> bool`

### `notifier.py`
- `notify_desktop(message)`
- `notify_email(message)`

### `checker.py`
- CLI definition (click)
- Polling loop with date range iteration
- Mode dispatch (notify vs autobook)
- Token refresh on 401
