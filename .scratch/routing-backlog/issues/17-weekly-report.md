# 17 — The weekly report: improvement as a fact you read

**What to build:** A short Markdown report, written weekly beneath the injected root, that renders
the scoreboard as something a human reads in one sitting: each metric, its direction since last week,
every change adopted, every change reverted, and every budget degradation the week's journal recorded.

Plain Markdown, no tooling to view it. This is the canonical record of what the loop did — the live
dashboard queued in the spec's Out of Scope is a view over the same data, never a second source of
truth.

**Blocked by:** 16

**Status:** done — commit `00438f7` (`learning_report.py`, `test_learning_report.py`, CI wiring;
includes the post-implementation Standards-review fix, landed before the first commit rather than
as a separate one). Plan at `.scratch/plans/17-weekly-report.md`.

- [x] A weekly report is written as Markdown beneath the injected root directory.
- [x] It contains every metric family with its direction since the previous report.
- [x] It lists every change adopted this week and every change reverted, each on its own line.
- [x] It lists every budget degradation recorded in the week's journal.
- [x] A week with no journal activity produces a report saying so rather than an empty file or an
      error.
- [x] Tests assert on report contents for a populated week and a quiet one.

Adopted/reverted have no producer yet (tickets 20/21) — rendered as "Not yet wired" rather than a
measured zero, the same no-data-vs-zero discipline ticket 16 established for metrics, applied here
to a list. Design record: `.scratch/plans/17-weekly-report.md` (three Planner-Critic rounds) and
`.scratch/planning_debate.md` (full transcript, including the critic-tier fallback chain).
