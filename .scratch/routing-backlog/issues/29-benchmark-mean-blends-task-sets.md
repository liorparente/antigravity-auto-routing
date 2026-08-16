# 29 — `mean_benchmark_score` blends incomparable task sets

**What to build:** A rule for what `learning_scoreboard._replay_benchmark_metrics`
(`skills/worker-routing/learning_scoreboard.py`) does when the window holds `ReplayBenchmarkRecord`s
from more than one `task_set`, and the implementation of whatever that rule turns out to be.

Today it averages every windowed record whose `success` is `True`, regardless of `task_set`:

```python
windowed = _windowed(replay_benchmarks, window_start=window_start, now=now)
scores = [record.score for record in windowed if record.success]
```

Ticket 26 gave the record a `task_set` field precisely so a score would carry "the identity of the
task set scored". Nothing reads it. It is written, validated, journaled — and then dropped on the
floor by the only consumer the scoreboard has.

**Why this bites, and when.** A benchmark task set is versioned because benchmarks get revised: a
harder `bench-v2` scores lower than `bench-v1` on an unchanged system, an easier one scores higher.
Either way, the day the task set changes, the trailing window holds both, and the mean moves for a
reason that has nothing to do with the system's quality. `acceptance_gate.evaluate_proposal` then
reads that movement through `compare_scoreboards` as `regressed` and rejects every proposal it is
handed until the old task set ages out of the window — a multi-day, entirely silent stall of the
learning loop, whose only symptom is proposals being declined for a regression nobody caused. The
weekly report renders the same blended number as a trend line.

**The decision this ticket owns.** The fix is not obvious and the tickets that shipped the pieces do
not make it. At least three defensible rules:

- **Latest task set only.** Compute the mean over the most recent `task_set` present in the window
  and ignore older ones. Simple, self-healing on a version bump, and discards real evidence.
- **Segment and compare like-for-like.** Keep a mean per `task_set`; `compare_scoreboards` pairs only
  matching identities and reports `indeterminate` where a baseline has no counterpart. Truthful, and
  the largest change — `Scoreboard`'s metric-name uniqueness guard assumes one metric per name.
- **The caller names the task set.** `read_scoreboard`/`compute_scoreboard` take the `task_set` whose
  trend is being asked about, and the gate passes its own. Smallest scoreboard change, pushes the
  choice onto every caller, and leaves the weekly report needing a default anyway.

Whichever is chosen, `mean_benchmark_score` is ticket 16's metric and `ReplayBenchmarkRecord` is
ticket 26's schema — this ticket changes the consumer, not the record.

**Origin:** Spec-axis `/code-review` finding during the tickets 18/26 convergence loop. Deliberately
not fixed in that loop: every option above is a policy decision on another ticket's metric, and
picking one inside a convergence pass is how scope leaks. Ticket 26's own acceptance criterion is
satisfied as written — it asks that a record *can carry* the identity, which it does.

**Blocked by:** none (16 and 26 are done; this is a follow-on against their shipped code)

**Status:** complete

- [x] A rule is chosen from the options above (or a better one), and the reasoning is recorded in
      `_replay_benchmark_metrics`' docstring — not just the mechanism.
- [x] `_replay_benchmark_metrics` implements it; `task_set` is read by a consumer for the first time.
- [x] A test drives the actual failure: a window holding two task sets, where the blended mean would
      read `regressed` and the corrected one does not.
- [x] `acceptance_gate.evaluate_proposal` is exercised across a task-set change and does not reject a
      good proposal for the version bump alone.
- [x] The weekly report's replay-benchmark section stays correct under the new rule, including its
      `sample_size`.
