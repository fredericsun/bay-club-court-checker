# Bay Club Court Checker — Harness

A multi-agent Python harness that autonomously builds a Bay Club court availability checker via a Planner → Generator → Evaluator loop.

## How it works

You provide one prompt. The orchestrator does the rest:

1. **Planner** — turns your prompt into a spec and sprint breakdown
2. **Generator** — builds the app sprint by sprint
3. **Evaluator** — tests each sprint; loops back if it fails

Each agent is a separate `claude -p` subprocess. They communicate only through files in `workspace/`.

## Run the harness

```bash
python orchestrator.py "build me a Bay Club tennis court availability checker"
```

Re-running resumes from the last completed sprint (reads `workspace/state.json`).  
To force a full restart: `rm workspace/state.json`

## Architecture

```
workspace/
├── spec.md                  ← Planner writes this
├── sprints.json             ← Planner's sprint breakdown
├── state.json               ← Orchestrator resumability (gitignored)
├── build_summary.md         ← Generator's self-review
├── evaluation_report.json   ← Evaluator's verdict
└── implementation/          ← Generated app lives here
```

## Cost

Each sprint costs ~$1–5 in Claude API usage. The harness enforces `MAX_RETRIES = 3` per sprint and will exit rather than loop forever.

---

The generated checker tool is documented in [`workspace/implementation/README.md`](workspace/implementation/README.md).
