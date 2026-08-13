# 15 — The audit verdict stops evaporating

**What to build:** `routing_check.py` already computes everything needed — violations with their
issue codes (`DEC-01`..`DEC-05`, `LOG-01`), write counts, worker-call counts, routing declarations,
declaration drift. Then it prints them and the session ends and the numbers are gone. Protocol
discipline has no trendline because nothing keeps yesterday's verdict.

This ticket persists the audit verdict as a compliance record per session. The audit's own output
format and exit codes do not change — it gains a second destination, not a new contract.

**Blocked by:** 12

**Status:** done

- [x] Running the audit appends one compliance record for the audited session.
- [x] The record carries the violation count, the distinct issue codes raised, and the audit's
      metrics.
- [x] The audit's existing stdout output and its exit-code contract are unchanged.
- [x] A clean session records a clean verdict rather than recording nothing, so the trendline has no
      silent gaps.
- [x] The record is written beneath the injected root directory, never to the real repository during
      tests.
- [x] Tests cover a violating session and a clean one.

Delivered by commit `23a138c`, after two fix rounds: persistence defaulting to `Path.cwd()` let the
test suite write fixture audits into the real journal, and the follow-up fix then defaulted the root to
`$HOME`, splitting compliance records away from the other families. Both are now pinned by tests.

Three review rounds followed, landing in `e75efc7`, `085a490`, `6ebcb8f`. The two that changed the
design rather than a line: `install.sh` never propagated the new modules, so every consultation on an
installed harness returned `worker_error` while every dev-checkout test stayed green; and `session_id`
carried the sensitivity gate `task_id` had been exempted from — the same bug as ticket 12's, in its
second location, found only because a second review round was run over already-reviewed code.
`RunIdentity` was added so rework is countable at all.
