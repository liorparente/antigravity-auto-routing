# 33 — Memory lessons don't accumulate across sessions/runs; each `apply_memory_lesson` call replaces

**What to build:** nothing yet — this is a design question to resolve, not a ready-for-agent fix. It
should be settled (in `learned_state.py` and/or `risk_tiered_application.py`, whichever owns the
answer) before any caller starts invoking `learner_worker.run_session_end_light`/`run_weekly_deep`
back-to-back across many sessions in production.

**What it does now.** `run_session_end_light` and `run_weekly_deep` each fold every memory lesson
*one call* produces into a single `risk_tiered_application.apply_memory_lesson` call — consolidation
that is intra-run only, documented on `run_session_end_light`'s own docstring. `apply_memory_lesson`
goes through `learned_state.adopt`, which replaces the current memory version wholesale (see
`run_session_end_light`'s docstring on why lessons within one run are joined into one call rather than
applied one at a time — the same wholesale-replace behavior, one level up). Nothing merges a new run's
lessons with a prior run's: session 2's `apply_memory_lesson` call overwrites session 1's memory
version exactly the way a second lesson within one run would have overwritten the first, before this
ticket's consolidation fix.

**Why this matters.** The two light-cadence runs this ticket implements are meant to run once per
session, indefinitely, over a project's lifetime — `run_weekly_deep` similarly every week. If each
run's lessons replace rather than merge with the last, only the most recent session's or week's
lessons ever survive in `learned_state`'s current memory version; everything institutional-memory was
supposed to accumulate from earlier runs is silently dropped the next time either cadence fires.
Whether that is actually a problem depends on unresolved design questions this ticket does not answer:

- Does `learned_state.read_current(root).get("memory")` get read back and prepended to a new lesson
  set before the next `apply_memory_lesson` call — i.e. does accumulation belong in the caller
  (`learner_worker`), or in `learned_state`/`risk_tiered_application` itself?
- If accumulation happens, does it grow unbounded, or does something dedupe/prune/summarize old
  lessons over time? An ever-growing memory blob fed into every future prompt has its own cost and
  signal-to-noise problems that a naive "always prepend" fix would introduce.
- Does `learned_state`'s versioned-history model (ticket 19) already give a caller everything it needs
  to reconstruct accumulated memory from the version chain, making an explicit merge unnecessary?

**Origin:** Ticket 22 convergence loop (`3cecc61`) Round 2 review pass, finding P-2 — flagged as a
design question during the fix for the *intra-run* consolidation gap this ticket documents, not a
gap in that fix itself.

**Blocked by:** a design decision on where accumulation (if any) belongs — `learned_state.py`,
`risk_tiered_application.py`, or `learner_worker.py`'s own callers.

**Status:** needs-design — not ready-for-agent until the questions above are resolved.
