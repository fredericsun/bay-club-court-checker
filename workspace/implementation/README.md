# Bay Club Court Checker — Tool

Polls the Bay Club Connect portal for open tennis (or pickleball) court slots and sends a desktop notification when one opens.

Uses the Bay Club internal REST API (`connect-api.bayclubs.io`) directly after authenticating via Playwright. No HTML scraping.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your Bay Club login:

```bash
cp .env.example .env
```

```ini
# .env
BAY_CLUB_USERNAME=your_username
BAY_CLUB_PASSWORD=your_password

# Optional: email notifications
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=sender@gmail.com
# SMTP_PASSWORD=app-password
# NOTIFY_EMAIL=notify@example.com
```

The `.env` file is gitignored and never committed.

## Usage

### Check once

```bash
python checker.py --date 2026-04-05 --time-start 07:00 --time-end 10:00 --once
```

### Poll continuously (every 5 minutes)

```bash
python checker.py --date 2026-04-05 --time-start 07:00 --time-end 10:00 --interval 300
```

Press Ctrl-C to stop.

## CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--location` | `santa clara` | Club to check (e.g. `santa clara`, `redwood shores`) |
| `--court-type` | `tennis` | Sport: `tennis`, `pickleball`, or `squash` |
| `--players` | `Singles` | `Singles`, `Doubles`, or `Ball machine` |
| `--duration` | `60` | Session length in minutes: `30`, `60`, or `90` |
| `--date` | today | Date to check in `YYYY-MM-DD` format |
| `--time-start` | `00:00` | Start of time window (24h, inclusive) |
| `--time-end` | `23:59` | End of time window (24h, exclusive) |
| `--interval` | `300` | Seconds between polls |
| `--once` | off | Run one check and exit |

## How authentication works

The checker logs into `bayclubconnect.com` using a headless Chromium browser (Playwright) with your `BAY_CLUB_USERNAME` and `BAY_CLUB_PASSWORD`. It intercepts the OAuth token issued by `authentication2-api.bayclubs.io`, then uses that token for all subsequent API calls directly — no browser needed for polling.

Credentials are loaded from `.env` and never logged or hard-coded.

## How availability is checked

After login, the checker calls:

```
GET https://connect-api.bayclubs.io/court-booking/api/1.0/availability
    ?clubId=<id>&date=<date>&categoryCode=tennis
    &categoryOptionsId=<id>&timeSlotId=<id>&tennisCourtTypeCode=outdoor
```

The `clubId`, `categoryOptionsId`, and `timeSlotId` are resolved dynamically from the `/context` and `/filterContext` APIs based on your `--location`, `--players`, and `--duration` flags.

Available slots are returned in `availableTimeSlots` as `timeFromInMinutes` / `timeToInMinutes` values.

**Note:** The API only allows booking up to 3 days ahead.

## Notifications

- **macOS desktop**: fires automatically via `osascript` when a slot is found
- **Email**: fires if all five SMTP env vars are set (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_EMAIL`)
- **Fallback**: if `osascript` is unavailable, prints `[MATCH FOUND] ...` to stdout

## If you get 401 errors

The API gateway key embedded in the Bay Club web app may have rotated. Run:

```bash
python get_api_key.py
```

This re-intercepts the current key from a live browser session and prints it. Update the `API_SUBSCRIPTION_KEY` constant in `checker.py` with the new value.
