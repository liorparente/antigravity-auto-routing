# 01 — Parameterize the consultation by occasion

**What to build:** `run_advisory_consultation_debate` and its supporting types in
`skills/worker-routing/advisory_consultation.py` become occasion-aware. Introduce an `occasion`
parameter (`ambiguity` / `plan-review` / `code-review` / `post-mortem`) that selects the mission
prompt built by `_build_planner_prompt` / `_build_critic_prompt` and the blocking stance, instead
of the single hardcoded ambiguity-classification prompt. The existing `ambiguity` occasion must
keep behaving exactly as it does today — this ticket only makes the seam, it does not yet wire the
new occasions' triggers (that is ticket 03) or the panel topology (ticket 05).

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `AdvisoryDebateResult` (or an equivalent occasion-aware result) carries which occasion produced it.
- [x] Mission-prompt selection is a function of `occasion`, not a single hardcoded prompt string.
- [x] The existing ambiguity-classification call sites and tests pass unchanged — same prompts, same
      outcomes, same call counts.
- [x] Plan-review and code-review occasions have prompt-building stubs wired to the new seam (mission
      content itself can be minimal; the routing/plumbing is what this ticket proves).
- [x] Full existing suite (`.venv/bin/python skills/worker-routing/test_routing.py`) still passes.

## Notes

Landed in commit `fc6b786`. `/code-review` (Standards + Spec axes) caught two real issues on the
first pass — `occasion` field placement risking a latent positional-arg API break, and a new test
pinning literal prompt wording against spec 0003's testing policy — both fixed, re-reviewed clean.
149 tests pass (144 pre-existing + 5 new; one of the original 6 new tests was the wording-pinning
test that got deleted in review).
