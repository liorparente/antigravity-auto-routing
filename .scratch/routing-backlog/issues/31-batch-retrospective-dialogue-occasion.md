# 31 — The weekly deep run's "batch retrospective" is one worker prompt, not a dialogue

**What to build:** A decision about whether `run_weekly_deep`'s retrospective should stay a single
one-shot `invoke_worker` call, or become a full multi-round `advisory_consultation` dialogue over a
`post-mortem`-shaped occasion — and, if the latter, the change that wires it up.

**What it does now.** `learner_worker.run_weekly_deep` renders one prompt
(`_render_weekly_deep_prompt`) from the scoreboard comparison and the week's windowed journal
records, sends it through the single `invoke_worker` seam once, and parses whatever JSON object comes
back into `routing_table_update`/`brief_update`/`memory_lessons`/`retrospective_summary`. Ticket 22's
own description calls this "a batch retrospective dialogue over the week's tasks" — but nothing in
the implementation is a dialogue: there is no second party, no round, no verdict, no
`DialogueQualityRecord` written for it. It is a single worker's single unreviewed opinion about a
week of evidence, dressed in the vocabulary of a review process that does not run here.

**The case that this is fine as one-shot.** The weekly run already tiers everything it proposes
through `risk_tiered_application` — a routing-table update still has to clear the acceptance gate
(Tier 2, real trials, real scoreboard comparison), a brief diff still sits `pending` for a human
(Tier 3), and only memory lessons auto-apply (Tier 1), which is the lowest-stakes tier by design. The
proposal *itself* being one worker's read of the week doesn't matter much if nothing downstream trusts
it uncritically — the tiering is the check, not a second opinion on the retrospective. Running a full
`advisory_consultation` dialogue (Planner + Critic, up to N rounds) is real cost — worker calls,
latency, journaled dialogue records — for a weekly cadence job whose actual authority is already
capped by the tier a proposal lands in.

**The case that it should be a real dialogue.** `advisory_consultation.py`'s whole reason to exist is
that a single worker's unreviewed judgment is exactly the failure mode spec 0004 built dialogues to
catch — see this repo's `post-mortem` occasion, which exists for retrospective-shaped work already.
A routing-table update that clears the acceptance gate on weak trials, or a brief diff that reads
plausible but embeds a bad generalization, would ride on nothing but one worker's synthesis of a
week's evidence before either of those checks ever run. And ticket 22's own naming ("batch
retrospective dialogue") suggests a dialogue was the original intent, not a one-shot prompt that
happens to return JSON.

**Origin:** Ticket 22 convergence loop (`3cecc61`) review pass, Category 2 — a design trade-off
flagged rather than resolved inline, since picking a side changes `run_weekly_deep`'s seam (one
`InvokeWorker` vs. `run_advisory_consultation_debate`'s dialogue seam) and is not a mechanical fix.

**Suggested handling:** this is an architecture decision, not an implementation task — run it through
`/council-review` or a Planner-Critic dialogue before writing code. If the decision is "keep it
one-shot," `learner_worker.py`'s module docstring and ticket 22's own description should stop calling
it a "dialogue" so the vocabulary matches the implementation.

**Blocked by:** none to decide

**Status:** complete

- [x] A decision is recorded (spec 0004, or an ADR) on whether the weekly retrospective is a one-shot
      worker prompt or a full `advisory_consultation` dialogue (settled as one-shot synthesis in ADR 0009).
- [x] If it becomes a dialogue, `run_weekly_deep` is wired through `run_advisory_consultation_debate`
      (or equivalent) with a `task_id` supplied, per this repo's Learning-Journal Ground-Truth
      Recording rule that every consultation invoked with recording in mind needs one, and a
      `DialogueQualityRecord` lands in the journal for the run (N/A — one-shot synthesis chosen).
- [x] If it stays one-shot, `learner_worker.py`'s module docstring, `run_weekly_deep`'s own docstring,
      and ticket 22's `Status` all stop describing it as a "dialogue" (updated to "synthesis" / "one-shot batch retrospective").
