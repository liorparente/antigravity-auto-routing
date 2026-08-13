# 05 — Sensitivity gate: no sensitive task reaches a cloud worker

**What to build:** Before a single worker is contacted, the task text is checked for secrets,
credentials or personal data. If it carries any, the consultation does not run: it halts and reports
that human approval is required. Planning is not a loophole through which sensitive text leaves the
machine.

This ticket can run in parallel with 03 and 04 — it needs only the entry point from 02.

**Blocked by:** 02

**Status:** done

- [x] A task carrying a secret, credential or personal-data marker results in **zero** worker
      invocations — asserted by the injected fake recording no calls at all.
- [x] The result states that human approval is required, and is distinguishable from both a consensus
      and a stalemate outcome.
- [x] The check runs before any worker call, not as a filter on the response.
- [x] The halt is recorded, so an operator can see that planning was blocked and why.

## Notes

Delivered 2026-08-11. `skills/worker-routing/advisory_consultation.py` and `test_routing.py` only.
Suite went 119 → 127 tests; `ruff` and `mypy` clean, all three gates verified by the orchestrator
independently of the worker's own report.

### Design decisions

**A fifth outcome, not a fold-in.** `AdvisoryOutcome` gains `"sensitivity_halt"` rather than reusing
`stalemate` or `worker_error`. It is a pre-flight refusal on the task text — no worker was ever
contacted — so it is neither a disagreement nor a failure to reach one. `consensus_reached` stays a
derived property and is `False` for it.

**The detector is duplicated on purpose.** `SENSITIVITY_MARKERS` mirrors
`agent_council.SENSITIVE_PATTERNS` instead of importing it. Importing `agent_council` would pull
`urllib.request`, `asyncio` and `fcntl` into a module whose docstring promises no HTTP client and
full offline exercisability, and these files are loaded by path rather than as a package, so the
import would need a `sys.path` hack. Same precedent the spec already set for `MAX_DEBATE_ROUNDS`.
What makes the duplication safe is `test_sensitivity_markers_are_a_superset_of_agent_council_patterns`
— the test file already loads both modules, so the drift guard costs the production modules no
coupling at all.

**The reason names the marker, never the secret.** `_detect_sensitivity_marker` returns the marker
constant, so the reported reason can explain the halt without repeating the task text or the value
that tripped it. Enforced by `test_halt_reason_never_leaks_task_text_or_matched_secret_value`.

**The pattern list was not broadened.** Adding PII regexes would change routing behaviour wherever
`agent_council.route_task` uses the same markers. Separate ticket. Consequence worth knowing: the
`secret` and `password` markers are broad substring matches, so a legitimate task like "plan the
password reset flow" halts and asks for approval. That is fail-closed, matches existing routing
behaviour, and was accepted knowingly. An explicit operator override is a natural ticket after 06.

**The stale plan is removed on this exit too**, like the other three non-consensus exits, preserving
the invariant that the artifact on disk is never staler than the result describing it.

### Review — Codex 5.6 Sol, effort high

Zero P0. Codex confirmed the module's central invariant holds: no path reports consensus without an
explicit Critic approval. Two findings, both **accepted as known and documented rather than fixed**,
on the user's decision:

- *P1 — the cleanup suffix can carry `root_dir`.* Real mechanism: if removing a stale
  `implementation_plan.md` fails, the message embeds `plan_path` and the `OSError`. But `root_dir` is
  caller-injected and never task-derived (the repo root in production), the path is inside the
  `OSError`'s own text anyway, and the behaviour predates this ticket on the other three exits.
  Redacting would discard the diagnostic that explains why a stale plan survived. Documented at
  `_remove_stale_plan_artifact` instead.
- *P2 — `max_rounds <= 0` plus sensitive text raises instead of halting.* Zero workers are contacted
  under either ordering, so the security property holds regardless; only the report differs. An
  exception is the louder halt, and reordering would hand a caller with a real call-site bug a
  plausible-looking result instead of the error naming their mistake. Documented in
  `run_advisory_consultation_debate`'s docstring instead.

### Unblocks

Ticket 06 (transcript and telemetry) — its fourth outcome now exists.
