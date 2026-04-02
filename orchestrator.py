#!/usr/bin/env python3
"""
Bay Club Court Checker — Multi-Agent Orchestrator

Drives three Claude subprocesses (Planner, Generator, Evaluator) via `claude -p`.
Agents communicate through files in workspace/. Resumable across interruptions.

Usage:
    python orchestrator.py "build me a Bay Club tennis court availability checker"
"""

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

WORKSPACE = pathlib.Path("workspace")
PROMPTS = pathlib.Path("prompts")
MAX_RETRIES = 3
PASS_THRESHOLD = 70  # Evaluator score required to pass a sprint


# ---------------------------------------------------------------------------
# Claude subprocess runner
# ---------------------------------------------------------------------------

def run_claude(prompt: str, label: str = "agent") -> str:
    """Run `claude -p <prompt>` as a subprocess and return stdout."""
    print(f"    [claude] running {label}...")
    result = subprocess.run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    [warn] {label} exited with code {result.returncode}")
        if result.stderr:
            print(f"    [stderr] {result.stderr[:500]}")
    return result.stdout


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    state_path = WORKSPACE / "state.json"
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {
        "user_prompt": "",
        "planning_done": False,
        "completed_sprints": [],
        "last_updated": None,
    }


def save_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    (WORKSPACE / "state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Sprint helpers
# ---------------------------------------------------------------------------

def get_sprints() -> list[dict]:
    sprints_path = WORKSPACE / "sprints.json"
    if not sprints_path.exists():
        return []
    return json.loads(sprints_path.read_text()).get("sprints", [])


def get_evaluation_report() -> dict:
    report_path = WORKSPACE / "evaluation_report.json"
    if report_path.exists():
        try:
            return json.loads(report_path.read_text())
        except json.JSONDecodeError:
            pass
    return {"score": 0, "passed": False, "verdict": "rework", "blocking_issues": []}


# ---------------------------------------------------------------------------
# Phase 1: Planner
# ---------------------------------------------------------------------------

def run_planner(user_prompt: str) -> None:
    print("\n[1/3] Planner — turning your prompt into a spec and sprints...")
    template = (PROMPTS / "planner_prompt.md").read_text()
    prompt = template.replace("{USER_PROMPT}", user_prompt)
    run_claude(prompt, label="Planner")

    # Verify outputs
    if not (WORKSPACE / "spec.md").exists():
        print("  [error] Planner did not write workspace/spec.md")
        sys.exit(1)
    if not (WORKSPACE / "sprints.json").exists():
        print("  [error] Planner did not write workspace/sprints.json")
        sys.exit(1)

    sprints = get_sprints()
    print(f"  [ok] spec.md written, {len(sprints)} sprint(s) planned")


# ---------------------------------------------------------------------------
# Phase 2: Generator + Evaluator loop
# ---------------------------------------------------------------------------

def run_sprint(sprint: dict, state: dict) -> bool:
    """
    Run Generator → Evaluator for one sprint, retrying up to MAX_RETRIES.
    Returns True if sprint passed, False if exhausted retries.
    """
    sprint_num = sprint["sprint"]
    features = ", ".join(sprint.get("features", []))
    print(f"\n  Sprint {sprint_num}: {features}")

    gen_template = (PROMPTS / "generator_prompt.md").read_text()
    eval_template = (PROMPTS / "evaluator_prompt.md").read_text()

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n    Attempt {attempt}/{MAX_RETRIES}")

        # --- Generator ---
        build_summary = WORKSPACE / "build_summary.md"
        build_summary.unlink(missing_ok=True)  # clear previous

        gen_prompt = gen_template + f"\n\n---\nCurrent sprint number: {sprint_num}\n"
        run_claude(gen_prompt, label=f"Generator (sprint {sprint_num})")

        if not build_summary.exists():
            print("    [warn] Generator produced no build_summary.md — skipping evaluation")
            continue

        # --- Evaluator ---
        eval_report = WORKSPACE / "evaluation_report.json"
        eval_report.unlink(missing_ok=True)

        eval_prompt = eval_template + f"\n\n---\nCurrent sprint number: {sprint_num}\n"

        # If retrying, pass blocking issues as context
        if attempt > 1:
            prev_report = get_evaluation_report()
            issues = prev_report.get("blocking_issues", [])
            if issues:
                issues_text = "\n".join(f"- {i}" for i in issues)
                eval_prompt += f"\nPrevious attempt blocking issues (fix these):\n{issues_text}\n"

        run_claude(eval_prompt, label=f"Evaluator (sprint {sprint_num})")

        report = get_evaluation_report()
        score = report.get("score", 0)
        verdict = report.get("verdict", "rework")
        print(f"    Score: {score}/100 | Verdict: {verdict}")

        if report.get("passed") and score >= PASS_THRESHOLD:
            print(f"    [pass] Sprint {sprint_num} passed")
            state["completed_sprints"].append(sprint_num)
            save_state(state)
            build_summary.unlink(missing_ok=True)
            return True

        blocking = report.get("blocking_issues", [])
        if blocking:
            print(f"    Blocking issues:")
            for issue in blocking:
                print(f"      - {issue}")

    print(f"    [fail] Sprint {sprint_num} failed after {MAX_RETRIES} attempts")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(user_prompt: str) -> None:
    WORKSPACE.mkdir(exist_ok=True)

    state = load_state()

    # --- Planning phase (idempotent) ---
    if state.get("planning_done") and (WORKSPACE / "sprints.json").exists():
        print("[skip] Planner already ran — resuming from previous state")
    else:
        run_planner(user_prompt)
        state["planning_done"] = True
        state["user_prompt"] = user_prompt
        state.setdefault("completed_sprints", [])
        save_state(state)

    # --- Sprint loop ---
    sprints = get_sprints()
    if not sprints:
        print("[error] No sprints found in workspace/sprints.json")
        sys.exit(1)

    total = len(sprints)
    print(f"\n[2/3] Building — {total} sprint(s) to complete")

    completed = state.get("completed_sprints", [])

    for sprint in sprints:
        sprint_num = sprint["sprint"]

        if sprint_num in completed:
            print(f"  [skip] Sprint {sprint_num} already passed")
            continue

        success = run_sprint(sprint, state)
        if not success:
            print(f"\n[error] Sprint {sprint_num} could not be completed. Exiting.")
            print("  Re-run to retry from this sprint.")
            sys.exit(1)

    # --- Done ---
    print("\n[3/3] All sprints complete!")
    print("\nYour Bay Club court checker is ready.")
    print("See workspace/implementation/ for the generated app.")
    print("See workspace/spec.md for usage instructions.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python orchestrator.py \"<prompt>\"")
        print('Example: python orchestrator.py "build me a Bay Club tennis court availability checker"')
        sys.exit(1)

    main(sys.argv[1])
