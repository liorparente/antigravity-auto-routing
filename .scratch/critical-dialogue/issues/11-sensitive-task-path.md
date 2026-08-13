# 11 — Sensitive-task path across all four occasions

**What to build:** The sensitivity gate (`_detect_sensitivity_marker` and its existing fail-closed
behavior) precedes every occasion, not just ambiguity. A sensitive task may hold a dialogue only
between local models from two local families; if the local runtime is unavailable, the consultation
fails closed and escalates to the human — exactly today's behavior, now proven across plan-review,
code-review, and post-mortem too.

**Blocked by:** 01, 05, 07.

**Status:** done

- [x] A sensitive task's plan-review, code-review, and post-mortem dialogues invoke zero cloud-family
      workers — the test fake records only local-family calls.
- [x] A sensitive task with the local runtime unavailable fails closed and escalates, for all four
      occasions, matching spec 0001's existing ambiguity-occasion behavior.
- [x] Sensitive-task panels (Complex tier) still span two distinct local families rather than
      reusing one local family for all three roles, where more than one local family is available.
- [x] Existing spec-0001 sensitivity tests for the ambiguity occasion are unchanged.

## Notes

Landed in `8e8cb96`. Closes spec 0003 — `docs/specs/0003-critical-dialogue.md` moved to
`Status: Implemented` in the same commit.

**The design decision worth remembering.** The ticket's headline and criterion 4 pull in opposite
directions: "a sensitive task may hold a dialogue" wants the dialogue to run, while "existing
spec-0001 sensitivity tests are unchanged" pins those tests to a halt. They reconcile on
`reachability_check`: without it the module has no way to establish that a local runtime exists,
so halting is the only honest answer and spec 0001's behaviour is preserved by construction
rather than by special-casing. With it, the local-only roster is reached by composing the existing
`is_family_reachable` seam — `is_local_family(family) and reachability_check(family)` — so
`resolve_roster` needed no change at all.

**Two leaks the ticket did not name, found during implementation and review.** Budget rung 2
substitutes `light_doer` into every seat, which resolves to the cloud model "Codex 5.6 Terra" — a
sensitive dialogue would have reached the cloud purely because a session ran up its budget. Rung 2
now degrades only effort for a sensitive task. And `dispatch_post_mortem_consultation` declined to
expose the roster seam on the stated grounds that "none has a post-mortem consumer today"; this
ticket is that consumer, so without threading it a sensitive post-mortem could only ever halt.
The first was caught while composing the mission brief, the second by the Spec-axis review.

**Review status.** Two-axis review converged over two rounds, but both rounds are same-family
degraded — `codex` was quota-exhausted, so Claude reviewed Claude's work. A `codex` Spec-axis
re-run is queued for after 2026-08-18.

Gates at landing: 307 tests (was 295), 10 production-invoker tests, `ruff` + `mypy` clean over the
seven CI files.
