# 44 — Visual Learning Report & Observability Dashboard

* **Category:** Observability & UI
* **Priority:** Medium (Recommended Step 4)
* **Status:** closed

## Problem Statement
`learning_journal.jsonl` contains rich empirical performance and outcome data across models and tasks, but inspecting it currently requires custom CLI queries or reading raw JSON Lines.

## Acceptance Criteria
- [x] Build a standalone report generator `skills/worker-routing/learning_report_html.py` (or CLI subcommand).
- [x] Produce an interactive standalone HTML dashboard rendering:
  - Token and financial cost savings relative to baseline single-model execution.
  - Success and rework rates per model family.
  - Summary of recent compliance audits and degradation events.
- [x] Integrate with existing `learning_report.py` and `learning_scoreboard.py`.
- [x] Add unit tests verifying HTML report compilation from fixture journals.

## Resolution (2026-08-23)

Added `skills/worker-routing/learning_report_html.py`, a pure, clock-free
sibling of `learning_report.py`/`learning_scoreboard.py` that renders a
standalone, zero-dependency HTML dashboard (light mode, RTL Hebrew/English
headings, embedded CSS, no external requests):

- **KPI cards** for all eight canonical `Scoreboard` metrics (current vs.
  baseline, improved/held/regressed/indeterminate), plus four derived
  metrics this module computes directly from the journal:
  `first_pass_yield`, `total_cost_usd`, and `cost_savings_usd` (a measured
  current-window-vs-baseline-window comparison — not a fabricated Tier-3
  rate, since no such rate is configured anywhere in this codebase), and
  `token_savings`, which stays permanently `MetricNoData`:
  `WorkerExecutionRecord` carries no token field and no producer journals
  one, mirroring `escalation_rate`'s existing permanent-no-data precedent
  rather than inventing a number.
- **Model family performance table**: executions, distinct tasks, total
  cost, success rate, rework rate, and mean duration — grouped by
  `model_family` over the current window.
- **Consensus/debate metrics**, **compliance audits** (session-reduced,
  last-record-wins), and **budget degradation events**, mirroring
  `learning_report.py`'s own reduction and windowing rules.
- Every dynamic value passes through a single `_escape` helper
  (`html.escape`) before reaching the template, as defense in depth — no
  journal field can carry an HTML metacharacter today (every string is
  `TASK_ID_RE`/`ISSUE_CODE_RE`/`Literal`-constrained), but the render
  boundary never trusts that going forward.
- `render_html_report(journal, board, baseline_board, *, now, window_days)`
  is the pure contract door (board/baseline_board precomputed by the
  caller, mirroring `render_weekly_report`'s own internal computation);
  `write_html_report(journal_path, output_path, *, now, window_days)` is
  the atomic-write convenience door (tempfile + `os.replace`, matching
  `learning_report._atomic_text_write`); `html_report_path` is the HTML
  sibling of `report_path`, landing in the same `.ralph/reports/`
  directory.
- `learning_report.py --html [PATH] --now <ISO-8601>` writes the dashboard
  alongside the Markdown report; `--html - --no-markdown` prints HTML only.
  The CLI requires an injected timestamp, preserving the report modules'
  no-live-clock invariant.
- Tests in `skills/worker-routing/test_learning_report_html.py`
  cover: the no-live-clock AST guard, the pure render contract (including
  board/baseline_board consistency validation), cost/savings/FPY
  arithmetic, the model family table, compliance/degradation
  listing, escaping, empty-journal handling, and the atomic writer
  (parent-dir creation, same-day supersede, no stray temp file on
  success, prior file untouched on a failed `os.replace`).
- Wired into CI (`PYTHON_MODULES`/`PYTHON_TESTS` in
  `.github/workflows/test.yml`) and into both harness-propagation scripts
  (`install.sh`'s `MANAGED_FILES`, `uninstall.sh`'s `INSTALLED_FILES`) —
  the repo's own four-list invariant for a new production module.
- Verified with `python3 -m unittest
  skills/worker-routing/test_learning_report_html.py`, `python3 -m unittest
  skills/worker-routing/test_learning_report.py`, and `python3 -m unittest
  discover skills/worker-routing`.
