# 03 — Wire the four occasion triggers

**What to build:** Extend `needs_advisory_consultation` (or add sibling predicates) so each
occasion fires on its own condition: plan-review at complexity ≥ Medium; code-review at complexity
≥ Medium always, plus risk signals at any tier (failing tests, a diff exceeding a configured size
threshold, changes touching security-sensitive paths — threshold and path patterns come from
config, not literals); post-mortem on every failure, the protocol's 2-failure escalation rule, and
every stalemate. The existing ambiguity predicate is untouched.

**Blocked by:** 01.

**Status:** done

- [x] Plan-review triggers at Medium and Complex, not at Simple/Trivial.
- [x] Code-review triggers at Medium+ unconditionally, and additionally at any tier when a risk
      signal is present (failing tests / oversized diff / security-sensitive path).
- [x] The diff-size threshold and the security-path patterns are read from config, not hardcoded.
- [x] Post-mortem triggers on failure, on the 2-failure escalation, and on a stalemate outcome from
      any occasion (including itself — a post-mortem does not recursively trigger another post-mortem).
- [x] The pre-existing ambiguity trigger predicate and its tests are unchanged.

## Notes

Landed in commit `7d65b01`. Four new predicates in `advisory_consultation.py`:
`needs_plan_review_consultation`, `needs_code_review_consultation`,
`needs_post_mortem_consultation` (`needs_advisory_consultation` untouched). Config lives in
`routing-config.json`'s new `critical_dialogue` section. `/code-review` found no hard violations;
one real drift risk (the 2-failure threshold hardcoded independently in two places) fixed via a
mirrored `ESCALATION_FAILURE_THRESHOLD` constant with a drift-guard test. 180 tests pass.
