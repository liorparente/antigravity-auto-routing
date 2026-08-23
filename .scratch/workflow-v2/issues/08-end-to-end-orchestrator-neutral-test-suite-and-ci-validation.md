# 08 — End-to-End Orchestrator-Neutral Test Suite & CI Validation

**What to build:** Validate the complete Workflow V2 lifecycle (Role resolution, Orchestrator neutrality across harnesses, 1-shot Council review, and TDD execution) across the full test suite in .github/workflows/test.yml with zero regressions.

**Blocked by:** 05 — Multi-Harness Protocol Synchronization in install.sh, 07 — Fast-Path 1-Shot Council Review & Security Veto Engine

**Status:** complete

- [x] Execute all 19 test suites in .github/workflows/test.yml and confirm 100% pass rate.
- [x] Run ruff check and mypy across all Python modules to verify zero lint or type errors.
- [x] Perform a dry-run mission invocation from Claude Code CLI and Codex CLI to verify orchestrator neutrality.
- [x] Verify git status is clean and all documentation matches implementation.
