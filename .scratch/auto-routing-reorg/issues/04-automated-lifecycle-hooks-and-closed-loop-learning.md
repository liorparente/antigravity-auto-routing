# 04 — Automated Ground-Truth Lifecycle Hooks & Closed-Loop Calibration

## What to build
Implement automated ground-truth recording hooks in `learning_outcomes.py`:
1. `auto_record_test_execution(task_id: str, exit_code: int, root_dir: Path)`: Automatically maps `exit_code == 0` to `passed=True` and appends an `OutcomeRecord(ground_truth="tests")` upon local TDD test completion.
2. `auto_record_review_execution(task_id: str, approved: bool, root_dir: Path)`: Automatically appends an `OutcomeRecord(ground_truth="review")` upon reviewer sign-off.

Enforce positional reduction where multiple records under the same `(task_id, ground_truth)` pair resolve to the latest record. Wire these verified outcomes into `learning_scoreboard.py` and `learned_state.py` to dynamically calibrate model scores and update router fallbacks in closed-loop fashion without human friction.

## Acceptance criteria
- [ ] Automated test completion triggers `auto_record_test_execution` and records honest boolean pass/fail in the learning journal.
- [ ] Code review sign-off triggers `auto_record_review_execution` and records review verdict.
- [ ] Positional reduction correctly reduces multiple outcome records per task in chronological order.
- [ ] Dynamic scoreboard and router calibration scores update automatically from verified outcomes.

## Blocked by
- 02 — Active LM Studio Capability Probing & Universal Calibrated Provider Routing
- 03 — 20 Golden Rules Memory Compaction & Scoped Keyword Retrieval
