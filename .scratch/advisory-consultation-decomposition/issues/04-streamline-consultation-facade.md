# 04 — Streamline Advisory Consultation Orchestrator & Final Hardening (Slice 4)

**What to build:** Refactor `advisory_consultation.py` into a clean, compact debate orchestrator (~300 lines) delegating to the three deep modules. Verify that all 911+ regression tests in `test_routing.py` pass cleanly, run linters (`ruff check`), type checks (`mypy`), and execute `./install.sh .` for multi-harness synchronization.

**Blocked by:** 03 — Extract Transcript Formatting & Telemetry Reporting (Slice 3).

**Status:** ready-for-agent

- [ ] Clean up `advisory_consultation.py`, ensuring it acts purely as a coordinator of the multi-round debate lifecycle.
- [ ] Verify that internal complexity is dramatically reduced while retaining full public API compatibility.
- [ ] Run full test suite (`python3 -m unittest skills/worker-routing/test_routing.py`) and ensure 100% green.
- [ ] Run linters (`ruff check skills/worker-routing/`) and strict type-checker (`mypy skills/worker-routing/`).
- [ ] Run `./install.sh .` to synchronize AGENTS.md, CLAUDE.md, and harness skill links.
