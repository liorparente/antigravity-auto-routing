# 52 — Automated Unit Tests & AST Invariants

**GitHub Issue:** [#28](https://github.com/liorparente/antigravity-auto-routing/issues/28)

**What to build:** A comprehensive suite of unit tests in `test_learning_report_html.py` and `test_learning_report.py` ensuring Role Matrix markup, model capability escaping, and server endpoints are 100% verified.

**Blocked by:** 51 — Local Dashboard Server & Atomic Save API

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Add unit tests verifying tab navigation and Role Matrix HTML markup generation.
- [ ] Add unit tests asserting dynamic model capability injection and strict escaping (`_escape`).
- [ ] Add unit tests for `--serve` argument parsing and API endpoint request validation.
- [ ] Enforce the no-live-clock AST guard invariant.
- [ ] Run and pass full test suite: `python3 -m unittest discover skills/worker-routing`.