# 44 — Visual Learning Report & Observability Dashboard

* **Category:** Observability & UI
* **Priority:** Medium (Recommended Step 4)
* **Status:** open

## Problem Statement
`learning_journal.jsonl` contains rich empirical performance and outcome data across models and tasks, but inspecting it currently requires custom CLI queries or reading raw JSON Lines.

## Acceptance Criteria
- [ ] Build a standalone report generator `skills/worker-routing/learning_report_html.py` (or CLI subcommand).
- [ ] Produce an interactive standalone HTML dashboard rendering:
  - Token and financial cost savings relative to baseline single-model execution.
  - Success and rework rates per model family.
  - Summary of recent compliance audits and degradation events.
- [ ] Integrate with existing `learning_report.py` and `learning_scoreboard.py`.
- [ ] Add unit tests verifying HTML report compilation from fixture journals.
