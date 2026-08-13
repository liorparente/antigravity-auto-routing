# 17 — The weekly report: improvement as a fact you read

**What to build:** A short Markdown report, written weekly beneath the injected root, that renders
the scoreboard as something a human reads in one sitting: each metric, its direction since last week,
every change adopted, every change reverted, and every budget degradation the week's journal recorded.

Plain Markdown, no tooling to view it. This is the canonical record of what the loop did — the live
dashboard queued in the spec's Out of Scope is a view over the same data, never a second source of
truth.

**Blocked by:** 16

**Status:** ready-for-agent

- [ ] A weekly report is written as Markdown beneath the injected root directory.
- [ ] It contains every metric family with its direction since the previous report.
- [ ] It lists every change adopted this week and every change reverted, each on its own line.
- [ ] It lists every budget degradation recorded in the week's journal.
- [ ] A week with no journal activity produces a report saying so rather than an empty file or an
      error.
- [ ] Tests assert on report contents for a populated week and a quiet one.
