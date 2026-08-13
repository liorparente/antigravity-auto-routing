# 04 — Stalemate halts, and failures fail closed

**What to build:** The two ways a consultation ends without agreement. When the rounds run out with no
approval, execution stops and the developer is handed both final positions plus explicit options for
resolving the dispute — the system does not pick a winner and does not emit a plan. When something
goes wrong instead, the consultation fails closed: an unreadable verdict counts as no approval, and a
worker that errors ends the consultation without a plan and surfaces the failure.

The property under test throughout is negative: **no path may report consensus that was not
explicitly granted.** That is the defect this whole feature exists to correct.

**Blocked by:** 03

**Status:** done

- [x] Reaching the round cap without approval produces a result reporting no consensus.
- [x] That result carries both final positions and three resolution options: approve the Planner's
      architecture, approve the Critic's, or escalate to a human.
- [x] No plan artifact is written on a stalemate, and the consultation does not select a winner.
- [x] A Critic response whose verdict cannot be parsed counts as not approved and is recorded as
      unparseable rather than silently retried as a rejection.
- [x] A worker invocation that raises ends the consultation with no plan artifact and the failure
      visible to the caller.
- [x] A test asserts that neither failure path can produce a result reporting consensus.

`AdvisoryDebateResult` gained a typed `outcome: Literal["consensus", "stalemate",
"unparseable_verdict", "worker_error"]` discriminator (`consensus_reached` stays derived from it,
true only for `"consensus"`). Verdict parsing became tri-state (`_parse_critic_verdict` returns
`"approved" | "revise" | "unparseable"`) so an unparseable response halts immediately instead of
being fed back to the Planner as a rejection — "VERDICT: REVISE" is unaffected and still drives the
ticket 03 revision loop. A stalemate now returns a frozen `AdvisoryStalemateReport` (both final
positions plus exactly three `AdvisoryResolutionOption`s) instead of picking a winner. Every
non-consensus exit (stalemate, unparseable verdict, worker error) removes any pre-existing
`implementation_plan.md` under `root_dir` via `_remove_stale_plan_artifact`, closing the defect
carried out of ticket 02. `invoke_worker` calls are now wrapped in `except Exception` (never
`BaseException`) per call site; a raise ends the loop with outcome `worker_error` and the exception
message on `result.error`, with rounds completed before the failure preserved in `result.rounds`.
The old dict-returning `generate_debate_stalemate_report` was replaced by the typed
`_build_stalemate_report`/`AdvisoryStalemateReport` structure it was meant to seed, rather than
left as an unreferenced duplicate.

Tests: 105 before, 116 after (7 added for the initial ticket: round-cap stalemate outcome,
stalemate carries both positions and three options, pre-existing plan removed across all three
non-consensus exits (subTest per exit), unparseable verdict halts without a second Planner call,
raising worker halts with a visible error, a raising worker after a completed round preserves that
round's history, and an explicit "neither failure path reports consensus" test covering
stalemate/worker_error/unparseable in one place). `.venv/bin/python skills/worker-routing/test_routing.py`,
`.venv/bin/ruff check`, and `.venv/bin/mypy` are all clean on `advisory_consultation.py` and
`test_routing.py`. No test contacts a network or spawns a process — the only new fake capability is
`_RecordingInvoker` accepting a scripted `Exception` to raise instead of a string response.

Codex Sol review (post-ticket) returned no P0/P1 findings and three P2s, all fixed (4 more tests,
112 -> 116):

- **Stale-plan cleanup could raise and mask the original failure.** `_remove_stale_plan_artifact`
  called `plan_path.unlink(missing_ok=True)` unguarded; if the plan path was a directory or its
  parent unwritable, `unlink()` raised before a result was ever returned, replacing a worker's real
  exception (or a well-defined stalemate/unparseable exit) with an unrelated `OSError`. Cleanup now
  catches `OSError` and returns a description of the problem instead of raising; callers fold that
  into `result.error` via a new `_combine_errors` helper, so a worker error that also fails to clean
  up still shows both the worker's message and the cleanup problem, and a cleanup failure can never
  produce `outcome="consensus"`.
- **`consensus_reached` was a mutable duplicate of `outcome`.** It was a second, independently
  writable constructor field on `AdvisoryDebateResult`, so a caller could construct or mutate a
  result claiming consensus while `outcome` said otherwise — enforced only by convention.
  `AdvisoryDebateResult` is now `@dataclass(frozen=True)`, `consensus_reached` is a read-only
  `@property` derived from `outcome == "consensus"` and is no longer accepted by the constructor;
  every existing read of `result.consensus_reached` keeps working unchanged.
- **A non-positive `max_rounds` was reported as a stalemate.** With `max_rounds=0` or negative the
  loop body never ran, no worker was ever invoked, and the function still returned
  `outcome="stalemate"` with an empty-vs-empty stalemate report — invalid configuration presented as
  a genuine Planner-Critic disagreement. `run_advisory_consultation_debate` now raises `ValueError`
  before the loop if `max_rounds < 1`, naming the offending value.

Tests: 112 before this pass, 116 after (4 added: `max_rounds` of 0 and -1 both raise `ValueError`
with zero worker calls recorded (subTest per value), a directory at the plan path during a
worker-error exit still surfaces the original worker message alongside the cleanup problem, the same
directory-at-plan-path failure during an unparseable-verdict exit does not raise, and
`consensus_reached` is asserted to track all four `outcome` values and to be unassignable —
`dataclasses.FrozenInstanceError` on both `result.consensus_reached = ...` and
`result.outcome = ...`). `.venv/bin/python skills/worker-routing/test_routing.py`,
`.venv/bin/ruff check`, and `.venv/bin/mypy` remain clean on `advisory_consultation.py` and
`test_routing.py`. No test contacts a network or spawns a process.

**Follow-up: `_parse_critic_verdict` loosened for REVISE only, never for APPROVE.** In production
a Critic that writes `VERDICT: REVISE.` with a trailing period, or appends its objection on the
same line (`VERDICT: REVISE - needs a rollback strategy`), was being read as unparseable and
aborting the whole consultation — brittleness in the parser, not a real Planner-Critic
disagreement. `_parse_critic_verdict` now delegates the REVISE branch to a new
`_is_tolerant_revise` helper: a first line counts as REVISE when it starts with `VERDICT: REVISE`
and the character immediately after that prefix is either absent (end of line) or non-alphanumeric.
That separator check is what a bare `str.startswith` would have missed — it is exactly what keeps
`VERDICT: REVISED PLAN ATTACHED` and `VERDICT: REVISEMENT` reading as unparseable rather than as a
revision request. The APPROVE branch received no equivalent change and still requires an exact,
case-normalized `VERDICT: APPROVE` match — every existing near-miss-APPROVE test still passes
unmodified. The asymmetry is deliberate and safe: the property this whole ticket exists to protect
is that no path may report consensus that was not explicitly granted. Loosening APPROVE would let a
malformed response manufacture a consensus nobody granted, which is exactly the defect this ticket
closed. Loosening REVISE cannot manufacture consensus — at worst a loosely-worded objection now
correctly continues the revision loop instead of prematurely aborting it, which is the behavior the
Critic asked for in the first place. The Critic prompt built by `_build_critic_prompt` still asks
for an exact verdict line; only the parser reading a real model's actual output became tolerant, so
the ask stays strict while the parser stays forgiving of how models around it will not obey it.

Tests: 116 before this follow-up, 119 after (3 added: a table-driven test that every tolerated
REVISE form — trailing period, colon, dash/em-dash plus trailing prose, and a lowercase
parenthetical — drives a real second Planner call carrying the Critic's text; a table-driven test
that the rejected near-misses (`VERDICT: REVISED PLAN ATTACHED`, `VERDICT: REVISEMENT`,
prose-only, whitespace-only) still halt as `unparseable_verdict` with no second Planner call; and a
test pinning the asymmetry directly — near-miss APPROVE forms stay `unparseable_verdict` with no
plan written while the matching near-miss REVISE forms drive a revision round, so a future refactor
that "tidies" the two branches into symmetry fails this test). `.venv/bin/python
skills/worker-routing/test_routing.py`, `.venv/bin/ruff check`, and `.venv/bin/mypy` remain clean on
`advisory_consultation.py` and `test_routing.py`. No test contacts a network or spawns a process.
