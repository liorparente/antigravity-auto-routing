# 05 — End-to-End Integration, Multi-Harness Sync & Council Review Gate

## What to build
Execute a full end-to-end integration and verification cycle across the modernized `auto-routing` codebase:
1. Run `install.sh` to stage and synchronize the lean protocol block and package structure across Antigravity, Claude Code, and Codex harnesses.
2. Run the complete test suite (1,000+ unit and integration tests) to ensure zero regressions across all dialogue, routing, learning, and scoring modules.
3. Perform an end-to-end real-world task verification: execute a routed task targeting LM Studio / `agy` Flash, verifying dynamic probe, execution, and automatic ground-truth recording.
4. Execute the multi-agent `Council Review` panel (Claude, Codex, Gemini) to perform an exhaustive architectural peer review and achieve unanimous sign-off for release v3.6.

## Acceptance criteria
- [x] 100% of unit and integration tests pass with zero regressions (1,162 tests passed).
- [x] `install.sh` cleanly synchronizes all three AI harnesses (`~/.gemini/`, `~/.codex/`, `.agents/`).
- [x] Real-world test task verifies end-to-end execution, local model probing, and automatic outcome recording.
- [x] Multi-agent `Council Review` panel returns unanimous approval.

## Blocked by
- 04 — Automated Ground-Truth Lifecycle Hooks & Closed-Loop Calibration
