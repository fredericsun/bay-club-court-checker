# Bay Club Tennis Court Availability Checker — Product Spec (v1)

## Core Features

### Authentication
- Read `BAY_CLUB_USERNAME` and `BAY_CLUB_PASSWORD` from environment variables; never hardcode credentials
- Log in to the Bay Club member portal using Playwright (async)
- Retry login up to 3 times on HTTP 5xx or network timeout before raising a fatal error
- Detect login failure (wrong credentials, account locked) and exit with a descriptive error message

### Court Availability Scraping
- Navigate to the tennis court reservations page for a specified location and date
- Parse the reservations page and extract available time slots as structured data: `{date, location, court_type, start_time, end_time, court_id}`
- Support filtering by `--court-type` (e.g., "tennis", "pickleball") and `--time-start`/`--time-end` window
- Design the scraping layer with an injectable page-fetcher so tests can substitute a local HTML fixture without a live account

### Notification
- Send a macOS desktop notification via `osascript` when a matching slot is found
- Optionally send an email via SMTP when `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `NOTIFY_EMAIL` env vars are all set
- If `osascript` is unavailable (non-macOS), fall back to printing to stdout with a `[MATCH FOUND]` prefix

### Polling Loop
- Poll availability on a configurable interval (`--interval`, default 300 seconds)
- Use `asyncio` sleep between polls; no drift accumulation
- Handle `KeyboardInterrupt` (Ctrl-C) cleanly: print "Stopped." and exit with code 0
- On transient network errors (timeout, connection reset), log a warning and continue polling; do not crash

### CLI
- Built with `click`
- Flags: `--location`, `--court-type`, `--date` (YYYY-MM-DD, default today), `--time-start` (HH:MM, 24h), `--time-end` (HH:MM, 24h), `--interval` (seconds, default 300), `--once` (run one check and exit, no loop)
- `--help` output is complete and accurate for all flags
- Validate `--date` format and reject invalid dates with a clear error

---

## Tech Stack

| Component | Choice | Justification |
|---|---|---|
| Language | Python 3.11+ | Async support, rich stdlib, team familiarity |
| Browser automation | `playwright` (async API) | Handles JS-heavy SPAs; reliable selectors; supports headless mode |
| CLI | `click` | Declarative flags, auto-generated `--help`, easy to test |
| Scheduling | `asyncio` + `time.sleep` | No external deps; sufficient for simple polling |
| Notifications | `subprocess` (`osascript`) + `smtplib` | No external deps; native macOS alerts |
| State | File-based (`workspace/state.json`) | No database needed; simple resume on restart |

---

## Definition of Done (v1)

### What the user runs

```bash
export BAY_CLUB_USERNAME="user@example.com"
export BAY_CLUB_PASSWORD="secret"

python workspace/implementation/checker.py \
  --location "SF-Olympic" \
  --court-type tennis \
  --date 2026-04-05 \
  --time-start 07:00 \
  --time-end 10:00 \
  --interval 300
```

### Expected output (no slots found)

```
[2026-04-01 08:00:00] Checking SF-Olympic for tennis on 2026-04-05 between 07:00–10:00...
[2026-04-01 08:00:02] No matching slots found. Next check in 300s.
```

### Expected output (slot found)

```
[2026-04-01 08:05:02] Checking SF-Olympic for tennis on 2026-04-05 between 07:00–10:00...
[2026-04-01 08:05:04] *** MATCH FOUND: Court 3 — 08:00–09:00 at SF-Olympic ***
[2026-04-01 08:05:04] Desktop notification sent.
[2026-04-01 08:05:04] Next check in 300s.
```

---

## Non-Goals (v1)

- No web UI or dashboard
- No multi-sport support beyond what is accessible on the tennis/pickleball reservations page
- No automatic booking — checker only; user books manually
- No SMS notifications
- No cross-platform desktop notifications (Linux/Windows) — macOS only; stdout fallback elsewhere
- No persistent notification deduplication across restarts (same slot may notify again after restart)
- No Docker or containerization
- No support for multiple Bay Club accounts simultaneously
- No historical slot tracking or analytics
