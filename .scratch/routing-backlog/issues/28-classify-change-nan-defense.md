# 28 — `_classify_change` has no independent defense against a NaN metric

**What to build:** `learning_scoreboard._classify_change` (`skills/worker-routing/learning_scoreboard.py`)
misclassifies a NaN-valued metric as `"improved"` under `lower_is_better` (and `"regressed"` under
`higher_is_better`) — the exact inversion the function's own docstring says it exists to prevent,
just triggered by IEEE-754 NaN comparisons (`current.value == baseline.value` and
`current.value > baseline.value` are both `False` for NaN, and `False == False` evaluates `True`)
instead of a bare `>`.

**Not currently exploitable.** `MetricValue.__post_init__` refuses to construct a NaN value through
the public API at all (`_validate_metric_value`), so nothing in this codebase can hand
`_classify_change` a NaN today — `compare_scoreboards` is safe in practice, not by the function's
own logic. Found and pinned down by `test_a_nan_metric_can_never_reach_a_comparison_as_an_improvement`
(`skills/worker-routing/test_learning_scoreboard.py`), which bypasses the frozen dataclass's
immutability via `object.__setattr__` (legal post-`__post_init__`) specifically to demonstrate what
`_classify_change` does when the upstream guard isn't the only thing protecting it.

**Why this is worth a defensive fix anyway:** the only thing standing between today's safe behavior
and a silent misclassification is `MetricValue`'s constructor — a single validation site with no
second line of defense. If that guard is ever weakened, bypassed, or a future `Metric`-like type is
added without the same validation (`learning_scoreboard.py`'s own `Metric = MetricValue |
MetricNoData` union could grow a third member), `compare_scoreboards` degrades silently rather than
loudly. A `math.isnan` guard at the top of `_classify_change` — returning `indeterminate`, matching
how the function already treats `MetricNoData` on either side — closes that gap independently of
`MetricValue`'s own validation, the same defense-in-depth discipline the rest of this module already
applies elsewhere (e.g. `_validate_metric_value`'s own non-finite check exists precisely so a bad
value can't poison a later stage, per that function's docstring).

**Origin:** filed during a `/code-review` pass over `origin/main...HEAD` (spanning tickets 16, 17,
25) — a Standards-axis fix to ticket 17's diff (unrelated to this gap) rewrote the test above to
actually exercise `_classify_change`, surfacing this as a byproduct. A second review round caught
that the rewritten test's comment claimed the gap was "flagged separately" with no artifact backing
that claim — this ticket is that artifact.

**Blocked by:** none (16 is done; this is a follow-on hardening ticket against its shipped code)

**Status:** ready-for-agent

- [ ] `_classify_change` returns `indeterminate` for a NaN-valued metric on either side, matching how
      it already treats `MetricNoData` on either side — never `improved` or `regressed`.
- [ ] The existing `test_a_nan_metric_can_never_reach_a_comparison_as_an_improvement` is updated to
      assert the new, corrected behavior (`indeterminate`), and its comment stops citing this ticket
      as a future fix and instead notes the fix landed here.
- [ ] A parallel case for `higher_is_better` is covered too — the docstring above the fix should name
      both directions, not just the one the original test happened to use.
- [ ] No change to `MetricValue`'s own NaN-rejection — this ticket adds a second, independent guard,
      it does not relax or replace the first one.
