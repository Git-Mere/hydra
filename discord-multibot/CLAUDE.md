## Team Workflow

Non-trivial work is done by a three-role Claude Code agent team. Default to this unless the task is trivial.

- **Director (Opus)** — the main session. Plans with the user, breaks work into sub-phases, dispatches each to the Coder via the Agent tool, and receives the Reviewer's report. Does not write feature code directly; owns planning, sequencing, and final sign-off. Updates PLAN.md when a phase completes.
- **Coder (Sonnet)** — spawned via the Agent tool with `model: sonnet`. Implements exactly one sub-phase as specified by the Director, then hands the diff to the Reviewer. Does not self-approve.
- **Reviewer (Sonnet)** — spawned via the Agent tool with `model: sonnet`. Checks the Coder's diff for correctness, dead/unnecessary code, optimization, and adherence to Locked Decisions + Coding Conventions. If it passes, reports to the Director; if not, sends it back to the Coder with specifics.

Flow: Director plans -> Coder implements -> Reviewer checks -> (pass) report to Director / (fail) back to Coder. One sub-phase per loop. `uv run pytest` must pass before the Reviewer approves.

**Loop cap:** the Coder<->Reviewer cycle is capped at **3 iterations** per sub-phase. If the Reviewer still requires changes after the 3rd Coder attempt, the Director STOPS the loop, reports the sticking point to the user, and waits for direction instead of iterating further.