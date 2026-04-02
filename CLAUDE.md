# CLAUDE.md — Bay Club Court Checker

## What this project is

A multi-agent harness. `orchestrator.py` drives three Claude subprocesses (Planner, Generator, Evaluator) via `claude -p`. Agents communicate through files in `workspace/`. The harness builds a Bay Club tennis court availability checker autonomously from a single user prompt.

## Roles

| Agent | Prompt file | Reads | Writes |
|---|---|---|---|
| Planner | `prompts/planner_prompt.md` | user prompt (injected) | `workspace/spec.md`, `workspace/sprints.json` |
| Generator | `prompts/generator_prompt.md` | `workspace/spec.md`, `workspace/sprints.json` | `workspace/implementation/`, `workspace/build_summary.md` |
| Evaluator | `prompts/evaluator_prompt.md` | `workspace/build_summary.md`, `workspace/sprints.json` | `workspace/evaluation_report.json` |

## Key constraints

- **No agent calls another agent.** They read/write files only.
- **Generator builds one sprint at a time.** The current sprint number is appended to its prompt by the orchestrator.
- **Evaluator must be harsh.** A score of 70+ means the sprint genuinely passed. Calibration examples are in the evaluator prompt.
- **`workspace/state.json` is sacred.** It enables resumability. Don't delete it mid-run.

## What the final app must do

- Log in to the Bay Club member portal using `BAY_CLUB_USERNAME` and `BAY_CLUB_PASSWORD` env vars
- Check for open tennis court slots at configurable locations and time windows
- Send a desktop notification (macOS `osascript`) or email when a slot opens
- Accept CLI flags: `--location`, `--court-type`, `--date`, `--time-start`, `--time-end`, `--interval`
- Run in a polling loop (default: every 5 minutes)
- Handle login failures, network errors, and portal layout changes gracefully

## Tech stack (for Generator to follow)

- Python 3.11+
- `playwright` (async) for portal automation
- `click` for CLI
- `smtplib` / `subprocess` for notifications
- `apscheduler` or simple `time.sleep` loop for scheduling
- No external databases — file-based state only

## Acceptance criteria checklist (for Evaluator)

Sprint 1 (auth + scraping):
- [ ] `BAY_CLUB_USERNAME` and `BAY_CLUB_PASSWORD` are read from env; never hardcoded
- [ ] Script can log in and reach the reservations page without crashing
- [ ] Available slots for a given date are returned as a list

Sprint 2 (notification + scheduling):
- [ ] Desktop notification fires when a slot matching criteria is found
- [ ] Script polls on `--interval` seconds without drifting or hanging
- [ ] Ctrl-C exits cleanly

Sprint 3 (CLI + config):
- [ ] All flags documented in `--help`
- [ ] `--location` filters correctly
- [ ] `--time-start` / `--time-end` filter correctly

## File naming

Generated app lives at `workspace/implementation/checker.py` (plus any supporting modules). Do not write outside `workspace/implementation/` during generation.

## Cost guard

The orchestrator enforces `MAX_RETRIES = 3` per sprint and will exit rather than loop forever. Do not override this without user approval.
